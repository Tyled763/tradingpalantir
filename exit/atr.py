# =========================
# exit/atr.py — TradingPalantir
# ATR (Wilder) bar-by-bar — для ATR-трейла в ride-режиме и
# нативной реплики `Calculate ATR Trade Risk Levels`.
# =========================
from __future__ import annotations

from typing import Dict, List, Optional


class ATR:
    def __init__(self, period: int = 14):
        self.period = period
        self.value: Optional[float] = None
        self._prev_close: Optional[float] = None
        self._warm: List[float] = []

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        tr = high - low
        if self._prev_close is not None:
            tr = max(tr, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self.value is None:
            self._warm.append(tr)
            if len(self._warm) >= self.period:
                self.value = sum(self._warm) / self.period
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


def atr_from_bars(bars: List[Dict], period: int = 14) -> Optional[float]:
    a = ATR(period)
    v = None
    for b in bars:
        v = a.update(float(b["high"]), float(b["low"]), float(b["close"]))
    return v


def atr_risk_plan(entry: float, stop: float, bars: List[Dict],
                  period: int = 14, mult: float = 3.0) -> Dict:
    """Реплика `Calculate ATR Trade Risk Levels` — риск-план в журнал."""
    v = atr_from_bars(bars, period)
    risk = entry - stop
    return {"atr": v, "atr_mult": mult,
            "risk_per_unit": round(risk, 8),
            "atr_stop_suggestion": round(entry - (v or 0) * mult, 8) if v else None,
            "stop_vs_atr": round(risk / v, 2) if v else None}
