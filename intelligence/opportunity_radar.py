# =========================
# intelligence/opportunity_radar.py — TradingPalantir
# Stage A главного флоу: скоринг ВСЕХ eligible-монет 1..100 →
# watchlist топ-20 → armed set (score >= SCORE_ENTRY_THRESHOLD).
#
# Composite score (детерминированная база):
#   ликвидность/объём 0-25 · momentum 0-20 · соц/новости 0-15 ·
#   перп-давление ±15 · сектор 0-10 · режим рынка 0-10 ·
#   firewall-гейт (fail → cap 40 / исключение)
# Дёшево по API: 1-2 батча CMC quotes + 1 trending + кэш перп/режима.
# Разбивка скора каждой монеты → журнал (прозрачность для судей).
# =========================
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config as C
from cmc.cmc_client import CMC
from cmc.mcp_client import CMCMcp, CMCMCPError
from marketdata.token_registry import TokenRegistry


@dataclass
class ScoredToken:
    symbol: str
    address: str
    pool: str
    cmc_id: int
    score: float
    components: Dict[str, float] = field(default_factory=dict)
    armed: bool = False
    volume_24h: float = 0.0
    change_24h: float = 0.0
    firewall: str = "unchecked"


class OpportunityRadar:
    def __init__(self, registry: TokenRegistry, cmc: Optional[CMC] = None,
                 mcp: Optional[CMCMcp] = None, derivatives=None, firewall=None):
        self.registry = registry
        self.cmc = cmc or CMC()
        try:
            self.mcp = mcp or CMCMcp()
        except CMCMCPError:
            self.mcp = None
        self.derivatives = derivatives   # DerivativesPressureEngine
        self.firewall = firewall         # TokenRiskFirewall (опц. на этапе скоринга)

    async def _social_hot(self) -> set:
        if self.mcp is None:
            return set()
        hot = set()
        try:
            for cat in await self.mcp.trending_narratives():
                for c in (cat.get("top_coins") or []):
                    s = c.get("symbol") if isinstance(c, dict) else None
                    if s:
                        hot.add(str(s).upper())
        except CMCMCPError:
            pass
        return hot

    async def scan(self, regime: Optional[Dict] = None) -> List[ScoredToken]:
        """Скорит все монеты реестра. Возвращает отсортированный список."""
        cand = self.registry.with_pools()
        if not cand:
            return []
        quotes = await self.cmc.quotes([e.symbol for e in cand])
        hot = await self._social_hot()
        perp = await self.derivatives.read() if self.derivatives else {"score_adj": 0}
        regime = regime or {"global_regime": "neutral", "risk_budget": "normal"}

        # перп-поправка масштабируется в компонент ±15
        perp_adj = max(-15.0, min(15.0, float(perp.get("score_adj", 0)) * 1.25))
        # режим: risk_on=10, neutral=6, risk_off=2
        regime_pts = {"risk_on": 10.0, "neutral": 6.0, "risk_off": 2.0}.get(
            regime.get("global_regime", "neutral"), 6.0)

        out: List[ScoredToken] = []
        for e in cand:
            q = quotes.get(e.symbol)
            if not q or not q.get("volume_24h"):
                continue
            vol = float(q["volume_24h"])
            chg = float(q.get("change_24h") or 0.0)
            if vol <= 0:
                continue

            # 1) ликвидность/объём 0-25 (log-шкала: $1M→~8, $100M→~17, $10B→25)
            liq_pts = max(0.0, min(25.0, (math.log10(vol) - 5.0) * 5.0))
            # 2) momentum 0-20: умеренный плюс — хорошо; экстрим — срез (перегрев)
            if chg <= 0:
                mom_pts = max(0.0, 8.0 + chg)            # лёгкий минус терпим
            elif chg <= 12:
                mom_pts = 8.0 + chg                      # 8..20
            else:
                mom_pts = max(4.0, 20.0 - (chg - 12) * 0.5)  # перегрев → штраф
            mom_pts = max(0.0, min(20.0, mom_pts))
            # 3) соц 0-15
            soc_pts = 15.0 if e.symbol.upper() in hot else 4.0
            comps = {
                "liquidity": round(liq_pts, 1),
                "momentum": round(mom_pts, 1),
                "social": round(soc_pts, 1),
                "perp": round(perp_adj, 1),
                "sector": 5.0,            # MVP: нейтрально (Tier-2: sector analysis)
                "regime": regime_pts,
            }
            # нормировка: 100 = все компоненты на максимуме
            # (25+20+15+12+5+10 = 87 raw); иначе порог 90 недостижим
            _RAW_MAX = 87.0
            score = max(1.0, min(100.0, sum(comps.values()) / _RAW_MAX * 100.0))
            out.append(ScoredToken(
                symbol=e.symbol, address=e.address, pool=e.pool, cmc_id=e.cmc_id,
                score=round(score, 1), components=comps,
                volume_24h=vol, change_24h=chg))

        out.sort(key=lambda t: t.score, reverse=True)
        return out

    async def funnel(self, regime: Optional[Dict] = None) -> Dict:
        """
        Полный Stage A: скоринг → watchlist (топ-20) → firewall по watchlist →
        armed (score>=порога и firewall approved).
        """
        scored = await self.scan(regime)
        watchlist = scored[:C.WATCHLIST_SIZE]
        for t in watchlist:
            if self.firewall is not None:
                fw = await self.firewall.check(t.symbol, t.address, t.pool)
                t.firewall = fw["status"]
                if fw["status"] == "rejected":
                    t.score = min(t.score, 40.0)        # cap по спеку
                elif fw["status"] == "approved_small":
                    t.score = min(t.score, 89.0)        # не armed, но в watchlist
            else:
                t.firewall = "skipped"
            t.armed = t.score >= C.SCORE_ENTRY_THRESHOLD
        armed = [t for t in watchlist if t.armed]
        return {"scored": scored, "watchlist": watchlist, "armed": armed}
