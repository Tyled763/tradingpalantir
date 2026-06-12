# =========================
# intelligence/opportunity_radar.py — TradingPalantir (Scoring v2)
# Stage A: двухпроходный скоринг всех eligible-монет 1..100 →
# watchlist топ-20 → armed (score >= адаптивного порога по режиму).
#
# Pass 1 (все 128, 1 батч CMC quotes):
#   liquidity 0-25  — log-шкала объёма, cap на ~$1B (не $10B)
#   momentum  0-20  — бленд 24h(60%)+7d(40%), перегрев штрафуется
#   → топ-30 кандидатов идут в Pass 2.
# Pass 2 (топ-30, GT-бары 15m+1H — ~60 запросов/rescreen, в лимите):
#   breakout_readiness 0-25 — близость к 96-барному хаю (10) +
#                             volume expansion 12/48 (10) + higher-lows 1H (5)
#   trend_alignment    0-15 — close vs EMA144 на 15m и 1H (обе=15/одна=8/0)
#   social/news        0-15 — trending-флаг (15) или свежие новости (кэш 2ч)
# Абсолютный максимум = 25+20+25+15+15 = 100 (честная шкала).
# Гибрид: final = 0.7·absolute + 0.3·percentile_rank·100.
# Порог armed — АДАПТИВНЫЙ (режим рынка), не входит в пер-монетный скор.
# Firewall: rejected→cap 40, approved_small→cap 89.
# =========================
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config as C
from cmc.cmc_client import CMC
from cmc.mcp_client import CMCMcp, CMCMCPError
from marketdata.token_registry import TokenRegistry

PASS2_SIZE = 30


@dataclass
class ScoredToken:
    symbol: str
    address: str
    pool: str
    cmc_id: int
    score: float = 0.0
    absolute: float = 0.0
    percentile: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)
    armed: bool = False
    volume_24h: float = 0.0
    change_24h: float = 0.0
    firewall: str = "unchecked"
    pass2: bool = False


def _ema(closes: List[float], period: int = 144) -> Optional[float]:
    if not closes:
        return None
    alpha = 2.0 / (period + 1)
    v = closes[0]
    for c in closes[1:]:
        v = alpha * c + (1 - alpha) * v
    return v


class OpportunityRadar:
    def __init__(self, registry: TokenRegistry, cmc: Optional[CMC] = None,
                 mcp: Optional[CMCMcp] = None, derivatives=None, firewall=None,
                 feed=None):
        self.registry = registry
        self.cmc = cmc or CMC()
        try:
            self.mcp = mcp or CMCMcp()
        except CMCMCPError:
            self.mcp = None
        self.derivatives = derivatives
        self.firewall = firewall
        self.feed = feed                      # GeckoTerminalFeed (Pass 2)
        self._news_cache: Dict[str, tuple] = {}   # sym -> (ts, pts)

    # ── Pass 1: дёшево, все монеты ────────────────────────
    @staticmethod
    def _pass1(vol: float, chg24: float, chg7d: Optional[float]) -> Dict[str, float]:
        # liquidity 0-25: $1M→3.5, $30M→14, $100M→17.5, ≥$1B→25
        liq = max(0.0, min(25.0, (math.log10(max(vol, 1.0)) - 5.5) * 7.0))
        # momentum 0-20: бленд 24h/7d; перегрев (>25%/24h) штрафуем
        m24 = chg24 if chg24 is not None else 0.0
        m7 = chg7d if chg7d is not None else 0.0
        blend = 0.6 * m24 + 0.4 * (m7 / 3.0)        # 7d масштабируем к дневному
        if blend <= 0:
            mom = max(0.0, 8.0 + blend)             # терпим лёгкий минус
        elif m24 > 25.0:
            mom = max(4.0, 18.0 - (m24 - 25.0) * 0.4)   # уже пампанул — поздно
        else:
            mom = min(20.0, 8.0 + blend * 1.2)
        return {"liquidity": round(liq, 1), "momentum": round(max(0.0, mom), 1)}

    # ── Pass 2: GT-бары, только топ-кандидаты ─────────────
    async def _pass2(self, t: ScoredToken) -> Dict[str, float]:
        out = {"breakout": 0.0, "trend": 0.0}
        if self.feed is None:
            return out
        try:
            b15 = await self.feed.bars(t.pool, "15m", limit=200, token=t.address)
            b1h = await self.feed.bars(t.pool, "1H", limit=200, token=t.address)
        except Exception:
            return out
        if len(b15) < 60 or len(b1h) < 30:
            return out

        closes15 = [float(b["close"]) for b in b15]
        last = closes15[-1]
        # breakout 0-25
        hi96 = max(float(b["high"]) for b in b15[-96:])
        prox = max(0.0, min(10.0, (1.0 - (hi96 - last) / hi96 / 0.05) * 10.0)) \
            if hi96 > 0 else 0.0                      # 10 = у хая, 0 = дальше 5%
        v_recent = sum(float(b["vol_usdt"]) for b in b15[-12:]) / 12.0
        v_base = sum(float(b["vol_usdt"]) for b in b15[-60:-12]) / 48.0
        vexp = max(0.0, min(10.0, (v_recent / v_base - 0.8) * 8.0)) if v_base > 0 else 0.0
        lows1h = [float(b["low"]) for b in b1h[-12:]]
        hl = 5.0 if (len(lows1h) >= 6 and
                     min(lows1h[-6:]) > min(lows1h[:6])) else 0.0
        out["breakout"] = round(min(25.0, prox + vexp + hl), 1)
        # trend 0-15: close vs EMA144 на 15m и 1H
        e15, e1h = _ema(closes15), _ema([float(b["close"]) for b in b1h])
        above = sum(1 for e in (e15, e1h) if e is not None and last > e)
        out["trend"] = {2: 15.0, 1: 8.0, 0: 0.0}[above]
        return out

    async def _social(self, t: ScoredToken, hot: set) -> float:
        if t.symbol.upper() in hot:
            return 15.0
        # свежие новости (кэш 2ч, только Pass2-кандидаты)
        c = self._news_cache.get(t.symbol)
        if c and time.time() - c[0] < 7200:
            return c[1]
        pts = 4.0
        if self.mcp is not None:
            try:
                news = await self.mcp.latest_news(t.cmc_id, limit=5)
                fresh = [n for n in news if n.get("title")]
                qual = [float(n.get("quality") or 0) for n in fresh]
                if len(fresh) >= 3 and qual and max(qual) >= 6:
                    pts = 12.0
                elif fresh:
                    pts = 8.0
            except (CMCMCPError, Exception):
                pass
        self._news_cache[t.symbol] = (time.time(), pts)
        return pts

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

    # ── полный скан ───────────────────────────────────────
    async def scan(self) -> List[ScoredToken]:
        cand = self.registry.with_pools()
        if not cand:
            return []
        quotes = await self.cmc.quotes([e.symbol for e in cand])
        hot = await self._social_hot()

        toks: List[ScoredToken] = []
        for e in cand:
            q = quotes.get(e.symbol)
            if not q or not q.get("volume_24h") or float(q["volume_24h"]) <= 0:
                continue
            t = ScoredToken(symbol=e.symbol, address=e.address, pool=e.pool,
                            cmc_id=e.cmc_id,
                            volume_24h=float(q["volume_24h"]),
                            change_24h=float(q.get("change_24h") or 0.0))
            t.components = self._pass1(t.volume_24h, t.change_24h,
                                       q.get("change_7d"))
            toks.append(t)

        # Pass 2 для топ-30 по Pass1
        toks.sort(key=lambda t: t.components["liquidity"] + t.components["momentum"],
                  reverse=True)
        for t in toks[:PASS2_SIZE]:
            t.pass2 = True
            t.components.update(await self._pass2(t))
            t.components["social"] = round(await self._social(t, hot), 1)
        for t in toks[PASS2_SIZE:]:
            t.components.setdefault("breakout", 0.0)
            t.components.setdefault("trend", 0.0)
            t.components["social"] = 15.0 if t.symbol.upper() in hot else 4.0

        # абсолют + перцентиль + гибрид
        for t in toks:
            t.absolute = min(100.0, sum(t.components.values()))
        ranked = sorted(toks, key=lambda t: t.absolute)
        n = max(1, len(ranked) - 1)
        for i, t in enumerate(ranked):
            t.percentile = i / n * 100.0
        for t in toks:
            t.score = round(max(1.0, min(100.0,
                            0.7 * t.absolute + 0.3 * t.percentile)), 1)
        toks.sort(key=lambda t: t.score, reverse=True)
        return toks

    async def funnel(self, threshold: float) -> Dict:
        """Stage A полностью: скан → watchlist → firewall → armed (адаптивный порог)."""
        scored = await self.scan()
        watchlist = scored[:C.WATCHLIST_SIZE]
        for t in watchlist:
            if self.firewall is not None:
                fw = await self.firewall.check(t.symbol, t.address, t.pool)
                t.firewall = fw["status"]
                if fw["status"] == "rejected":
                    t.score = min(t.score, 40.0)
                elif fw["status"] == "approved_small":
                    t.score = min(t.score, threshold - 1.0)
            else:
                t.firewall = "skipped"
            t.armed = t.score >= threshold
        return {"scored": scored, "watchlist": watchlist,
                "armed": [t for t in watchlist if t.armed],
                "threshold": threshold}
