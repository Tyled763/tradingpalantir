# =========================
# intelligence/market_regime_engine.py — TradingPalantir
# Нативная реплика скиллов `Detect Market Regime` + `macro liquidity monitor`
# + `daily market overview` (утренний брифинг) поверх CMC MCP.
#
# Выход: global_regime (risk_on/neutral/risk_off), market_state
# (trend_expansion/range_chop/liquidation_stress/unknown), risk_budget
# (normal/reduced/blocked) + EvidenceItem'ы.
# =========================
from __future__ import annotations

import time
from typing import Dict, Optional

from cmc.mcp_client import CMCMcp, CMCMCPError
from intelligence.evidence import EvidenceItem


class MarketRegimeEngine:
    def __init__(self, mcp: Optional[CMCMcp] = None, ttl_sec: int = 3600):
        try:
            self.mcp = mcp or CMCMcp()
        except CMCMCPError:
            self.mcp = None
        self.ttl = ttl_sec
        self._cache: Optional[Dict] = None
        self._cache_ts = 0.0

    async def read(self, force: bool = False) -> Dict:
        """Режим рынка с кэшем (брифинг — дорогой, обновляем по TTL)."""
        if not force and self._cache and time.time() - self._cache_ts < self.ttl:
            return self._cache
        regime = await self._compute()
        self._cache, self._cache_ts = regime, time.time()
        return regime

    async def _compute(self) -> Dict:
        out: Dict = {"global_regime": "neutral", "market_state": "unknown",
                     "risk_budget": "normal", "score": 0, "factors": {},
                     "evidence": []}
        if self.mcp is None:
            out["evidence"].append(EvidenceItem(
                source_skill="macro liquidity monitor", pipeline="morning_briefing",
                category="global_market_regime", status="blocked",
                summary="CMC MCP недоступен (нет ключа)").to_dict())
            return out

        # 1) macro liquidity (наша готовая реплика)
        try:
            macro = await self.mcp.macro_liquidity_monitor()
            out["global_regime"] = macro.get("regime", "neutral")
            out["score"] = macro.get("score", 0)
            out["factors"].update(macro.get("factors", {}))
            out["evidence"].append(EvidenceItem(
                source_skill="macro liquidity monitor", pipeline="morning_briefing",
                category="global_market_regime", confidence=0.6,
                directional_impact=out["global_regime"],
                risk_impact="reduce_risk" if out["global_regime"] == "risk_off" else "hold",
                summary=f"macro regime={out['global_regime']} factors={macro.get('factors')}",
                raw_payload=macro).to_dict())
        except CMCMCPError as e:
            out["evidence"].append(EvidenceItem(
                source_skill="macro liquidity monitor", pipeline="morning_briefing",
                category="global_market_regime", status="error",
                summary=f"ошибка: {e}").to_dict())

        # 2) market_state из факторов (Detect Market Regime — упрощённая реплика)
        mcap = out["factors"].get("total_mcap_change_24h")
        oi = out["factors"].get("derivatives_oi_change_24h")
        state = "unknown"
        if mcap is not None and oi is not None:
            if mcap < -4.0 and oi < -3.0:
                state = "liquidation_stress"          # резкий слив + деливеридж
            elif abs(mcap) < 1.0:
                state = "range_chop"
            elif mcap > 1.0 and oi > 0:
                state = "trend_expansion"
            elif mcap < -1.0:
                state = "risk_off_drift"
            else:
                state = "mixed"
        out["market_state"] = state

        # 3) risk budget
        if state == "liquidation_stress":
            out["risk_budget"] = "blocked"
        elif out["global_regime"] == "risk_off":
            out["risk_budget"] = "reduced"
        else:
            out["risk_budget"] = "normal"
        return out

    def adaptive_threshold(self, regime: Dict, perp: Optional[Dict] = None) -> float:
        """Порог armed: риск-он мягче, риск-офф жёстче; перегрев перпов +2."""
        import config as C
        thr = C.SCORE_THRESHOLDS.get(regime.get("global_regime", "neutral"),
                                     float(C.SCORE_ENTRY_THRESHOLD))
        if perp and perp.get("pressure") == "overheated":
            thr += C.SCORE_THRESHOLD_OVERHEAT_BUMP
        return thr
