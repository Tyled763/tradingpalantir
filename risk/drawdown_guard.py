# =========================
# risk/drawdown_guard.py — TradingPalantir
# Ступенчатый guard по дневной просадке (§18 спека):
#   >= DD_DEFENSIVE_PCT → defensive (риск ×0.5)
#   >= DD_BLOCK_PCT     → block_new_trades
#   >= DD_FLATTEN_PCT   → emergency_flatten (закрыть всё)
# =========================
from __future__ import annotations

from typing import Dict

import config as C


class DrawdownGuard:
    MODES = ("normal", "defensive", "block_new_trades", "emergency_flatten")

    def __init__(self):
        self.mode = "normal"

    def evaluate(self, daily_dd_pct: float) -> Dict:
        """daily_dd_pct — положительное число (% просадки от дневного якоря)."""
        prev = self.mode
        if daily_dd_pct >= C.DD_FLATTEN_PCT:
            self.mode = "emergency_flatten"
        elif daily_dd_pct >= C.DD_BLOCK_PCT:
            self.mode = "block_new_trades"
        elif daily_dd_pct >= C.DD_DEFENSIVE_PCT:
            self.mode = "defensive"
        else:
            self.mode = "normal"
        return {"mode": self.mode, "changed": self.mode != prev,
                "dd_pct": round(daily_dd_pct, 2),
                "risk_multiplier": 0.5 if self.mode == "defensive" else
                                   (0.0 if self.mode in ("block_new_trades",
                                                         "emergency_flatten") else 1.0)}

    @property
    def can_open(self) -> bool:
        return self.mode in ("normal", "defensive")

    @property
    def must_flatten(self) -> bool:
        return self.mode == "emergency_flatten"
