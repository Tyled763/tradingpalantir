# =========================
# intelligence/derivatives_pressure_engine.py — TradingPalantir
# Перп-рынок как КОНТЕКСТ влияния на цену (решение пользователя):
# не источник сделок, а (1) boost/penalty в скоринге, (2) аргумент в
# подтверждении входа, (3) exit-warnings при ведении позиции.
#
# Нативная реплика `perp contract analysis`/`Analyze Taker Flow Imbalance`/
# `Detect Funding Rate Regime Shift` поверх CMC MCP global derivatives.
# Пер-токен лейны на Basic недоступны → честный partial (global-уровень).
# =========================
from __future__ import annotations

import time
from typing import Dict, Optional

from cmc.mcp_client import CMCMcp, CMCMCPError
from intelligence.evidence import EvidenceItem


class DerivativesPressureEngine:
    def __init__(self, mcp: Optional[CMCMcp] = None, ttl_sec: int = 1800):
        try:
            self.mcp = mcp or CMCMcp()
        except CMCMCPError:
            self.mcp = None
        self.ttl = ttl_sec
        self._cache: Optional[Dict] = None
        self._cache_ts = 0.0

    async def read(self) -> Dict:
        """
        Глобальное перп-давление: {pressure: aligned|overheated|deleveraging|neutral,
        score_adj: -15..+15, evidence}.
        """
        if self._cache and time.time() - self._cache_ts < self.ttl:
            return self._cache
        out: Dict = {"pressure": "neutral", "score_adj": 0, "factors": {}, "evidence": []}
        if self.mcp is None:
            out["evidence"].append(EvidenceItem(
                source_skill="perp contract analysis", pipeline="derivatives_pressure",
                category="derivatives_pressure", status="blocked",
                summary="CMC MCP недоступен").to_dict())
            self._cache, self._cache_ts = out, time.time()
            return out
        try:
            d = await self.mcp.derivatives_metrics()
            oi = _num(d, ("totalOpenInterest", "percentage_change_24h"))
            vol = _num(d, ("totalVolume", "pct_change_prev_24h_vs_prior_24h"))
            out["factors"] = {"oi_chg_24h": oi, "vol_chg_24h": vol}
            if oi is not None:
                if oi > 5 and (vol or 0) > 10:
                    out["pressure"], out["score_adj"] = "overheated", -8   # перегрев лонгов
                elif oi > 2 and (vol or 0) > 0:
                    out["pressure"], out["score_adj"] = "aligned", +12     # сильная поддержка OI
                elif oi > 0:
                    out["pressure"], out["score_adj"] = "aligned", +8      # рост OI = поддержка
                elif oi < -3:
                    out["pressure"], out["score_adj"] = "deleveraging", -12  # деливеридж
                else:
                    out["pressure"], out["score_adj"] = "neutral", 0
            out["evidence"].append(EvidenceItem(
                source_skill="Analyze Taker Flow Imbalance", pipeline="derivatives_pressure",
                category="derivatives_pressure", status="partial",   # global-level only
                confidence=0.5, directional_impact=out["pressure"],
                risk_impact="reduce_risk" if out["pressure"] in ("overheated", "deleveraging") else "hold",
                summary=f"global perp: {out['pressure']} (OI {oi}%, vol {vol}%)",
                missing_inputs=["per-token funding", "per-token OI"],
                raw_payload=out["factors"]).to_dict())
        except CMCMCPError as e:
            out["evidence"].append(EvidenceItem(
                source_skill="perp contract analysis", pipeline="derivatives_pressure",
                category="derivatives_pressure", status="error",
                summary=f"ошибка: {e}").to_dict())
        self._cache, self._cache_ts = out, time.time()
        return out

    def exit_warning(self, pressure: Dict) -> Optional[str]:
        """Деливеридж = ранний флаг для открытых лонгов (EXIT_WARNING в журнал)."""
        if pressure.get("pressure") == "deleveraging":
            return "global deleveraging: OI падает — риск продолжения слива"
        return None


def _num(d, path):
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    try:
        return float(str(cur).replace(",", "").replace("%", "").replace("+", ""))
    except (TypeError, ValueError):
        return None
