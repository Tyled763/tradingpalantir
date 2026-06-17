# =========================
# risk/risk_governor.py — TradingPalantir
# Финальная детерминированная инстанция (§18): КАЖДОЕ исполняемое действие
# проходит approve(). LLM не может его обойти (capability-gating).
# Оборачивает портированный risk/core.py (RuleBook/RiskManager/sizing)
# + ступенчатый DrawdownGuard.
# =========================
from __future__ import annotations

from typing import Dict, Optional

import config as C
from risk.core import RuleBook, calc_size_spot, notional
from risk.drawdown_guard import DrawdownGuard
from journal import event_types as ET
from journal.trade_journal import TradeJournal


class RiskGovernor:
    def __init__(self, rules: RuleBook, journal: TradeJournal):
        self.rules = rules
        self.journal = journal
        self.guard = DrawdownGuard()

    def update_drawdown(self, daily_dd_pct: float) -> Dict:
        res = self.guard.evaluate(daily_dd_pct)
        if res["changed"]:
            self.journal.log(ET.DRAWDOWN_TRIGGERED, mode=res["mode"],
                             dd_pct=res["dd_pct"])
        return res

    def approve_entry(self, *, symbol: str, address: str, entry: float,
                      stop: float, open_positions: int,
                      size_factor: float = 1.0,
                      held_symbols: Optional[set] = None) -> Dict:
        """Полная проверка входа → {approved, qty, risk_usdt, reasons} либо отказ."""
        reasons = []

        # 1) allowlist (eligible-токены)
        allowed = {a.lower() for a in self.rules.allowed_tokens} if self.rules.allowed_tokens else None
        if allowed is not None and address.lower() not in allowed:
            return self._reject(symbol, f"{symbol} не в allowlist")
        reasons.append("token allowlisted")

        # 1b) одна монета — одна позиция (без пирамидинга/дублей по ТФ)
        if (getattr(self.rules, "one_position_per_symbol", True)
                and held_symbols and symbol in held_symbols):
            return self._reject(symbol, f"{symbol} уже в позиции (one-per-symbol)")

        # 2) drawdown guard
        if not self.guard.can_open:
            return self._reject(symbol, f"guard={self.guard.mode}: новые входы заблокированы")
        risk_mult = 0.5 if self.guard.mode == "defensive" else 1.0
        reasons.append(f"guard={self.guard.mode}")

        # 3) лимит позиций
        if open_positions >= self.rules.max_concurrent_positions:
            return self._reject(symbol, f"лимит позиций {self.rules.max_concurrent_positions}")

        # 4) валидность стопа (long-only спот)
        if stop >= entry:
            return self._reject(symbol, "стоп не ниже входа (long-only)")

        # 5) sizing: риск × guard × LLM size_factor (только вниз)
        size_factor = max(0.0, min(1.0, size_factor))
        risk_usdt = self.rules.max_risk_per_trade_usdt * risk_mult * size_factor
        if risk_usdt <= 0:
            return self._reject(symbol, "нулевой риск после поправок")
        qty = calc_size_spot(entry, stop, risk_usdt,
                             fee=C.ROUNDTRIP_FEE, slippage=C.EXPECTED_SLIPPAGE)
        notion = notional(entry, qty)
        # cap = половина книги; в defensive (risk_mult=0.5) дополнительно ужимаем
        # концентрацию вдвое (иначе при notional-cap-binds defensive был бы no-op)
        cap = self.rules.max_position_notional_usdt * risk_mult
        if notion > cap:
            qty = cap / entry
            notion = notional(entry, qty)
            reasons.append(f"ноционал срезан до cap ${cap:g}")
        if notion < self.rules.min_trade_notional_usdt:
            return self._reject(symbol, f"ноционал ${notion:.2f} < min ${self.rules.min_trade_notional_usdt}")

        approval = {"approved": True, "qty": qty, "risk_usdt": risk_usdt,
                    "notional": round(notion, 2), "risk_mode": self.guard.mode,
                    "reasons": reasons}
        self.journal.log(ET.RISK_APPROVED, symbol=symbol, **{
            k: v for k, v in approval.items() if k != "approved"})
        return approval

    def _reject(self, symbol: str, reason: str) -> Dict:
        self.journal.log(ET.RISK_REJECTED, symbol=symbol, reason=reason)
        return {"approved": False, "rejection_reason": reason}
