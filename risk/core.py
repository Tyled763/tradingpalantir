# =========================
# risk.py — BNB HACK Trading Agent
# Риск-менеджмент под критерии судейства Track 1:
# drawdown management · risk-adjusted performance · rule compliance.
#
# Чистая логика (без сети) → юнит-тестируется оффлайн.
#   - calc_size_spot: размер спот-позиции от риска и дистанции до стопа
#   - RiskManager:    портфельные лимиты + daily-drawdown circuit breaker
#   - RuleBook:       user-rules (вселенная, лимиты) + лог "правило → действие"
#   - PositionRegistry: персистентный реестр позиций (переживает рестарт)
# =========================
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Dict, List, Optional


# ── Размер позиции (спот, без плеча) ──────────────────────
def calc_size_spot(entry: float, stop: float, risk_usdt: float,
                   *, fee: float = 0.0025, slippage: float = 0.001) -> float:
    """
    Кол-во базового токена так, чтобы убыток при срабатывании стопа ≈ risk_usdt.

    Спот, без контрактов/плеча:
      loss_per_unit = |entry-stop| + (fee+slippage)·(entry+stop)
      qty = risk_usdt / loss_per_unit

    fee — суммарная комиссия round-trip (вход+выход), для PancakeSwap ~0.25%.
    slippage — ожидаемое проскальзывание на сторону.
    """
    per_unit = abs(entry - stop) + (fee + slippage) * (entry + stop)
    if per_unit <= 0:
        raise ValueError("calc_size_spot: некорректные entry/stop")
    if risk_usdt <= 0:
        raise ValueError("calc_size_spot: risk_usdt должен быть > 0")
    return risk_usdt / per_unit


def notional(entry: float, qty: float) -> float:
    """USDT-ноционал входа (сколько USDT тратим на покупку qty по entry)."""
    return entry * qty


# ── Состояние позиции ─────────────────────────────────────
class PosState(str, Enum):
    PENDING  = "pending"    # сигнал принят, ордер ещё не исполнен
    OPEN     = "open"       # в позиции, TP/SL выставлены
    CLOSED   = "closed"     # вышли (TP/SL/ручное)
    CANCELED = "canceled"   # вход не состоялся


@dataclass
class Position:
    sid: int
    symbol: str            # торговый токен, напр. "CAKE"
    direction: str         # "bull" (спот = только лонг; "bear" → skip на споте)
    entry: float
    stop: float
    tp: float
    qty: float
    risk_usdt: float
    state: str = PosState.PENDING.value
    setup: str = ""        # trend/breakout/reversal
    tf: str = ""           # ТФ позиции (для чтения trendflex на выходе)
    ride_mode: bool = False  # латч: confluence подтвердился → держим пока trendflex>0
    rationale: str = ""    # обоснование LLM
    opened_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    realized_pnl: Optional[float] = None
    tx_enter: Optional[str] = None
    avg_px: Optional[float] = None             # фактическая цена входа
    tp_automation_id: Optional[str] = None
    sl_automation_id: Optional[str] = None


# ── Персистентный реестр позиций ──────────────────────────
class PositionRegistry:
    """
    sid → Position, с атомарной записью на диск. Чинит известный баг
    «реестр в памяти теряется при рестарте» — критично для недельного live.
    """

    def __init__(self, path: str = "positions.json"):
        self.path = path
        self._positions: Dict[int, Position] = {}
        self._counter = 0
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r") as f:
            blob = json.load(f)
        self._counter = blob.get("counter", 0)
        for sid_str, d in blob.get("positions", {}).items():
            self._positions[int(sid_str)] = Position(**d)

    def _save(self) -> None:
        blob = {
            "counter": self._counter,
            "positions": {str(sid): asdict(p) for sid, p in self._positions.items()},
        }
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(blob, f, indent=2)
            os.replace(tmp, self.path)   # атомарно
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def new(self, **kwargs) -> Position:
        self._counter += 1
        pos = Position(sid=self._counter, **kwargs)
        self._positions[pos.sid] = pos
        self._save()
        return pos

    def update(self, sid: int, **changes) -> Position:
        pos = self._positions[sid]
        for k, v in changes.items():
            setattr(pos, k, v)
        self._save()
        return pos

    def get(self, sid: int) -> Optional[Position]:
        return self._positions.get(sid)

    def open_positions(self) -> List[Position]:
        return [p for p in self._positions.values()
                if p.state in (PosState.PENDING.value, PosState.OPEN.value)]

    def all(self) -> List[Position]:
        return list(self._positions.values())


# ── User-rules + лог соответствия ─────────────────────────
@dataclass
class RuleBook:
    """
    Декларативные правила пользователя. Агент обязан им подчиняться —
    каждое решение логируется (rule compliance — критерий судейства).
    """
    allowed_tokens: List[str] = field(default_factory=list)   # пусто = любой
    blocked_tokens: List[str] = field(default_factory=list)
    max_risk_per_trade_usdt: float = 20.0
    max_concurrent_positions: int = 3
    max_daily_drawdown_pct: float = 8.0        # circuit breaker
    long_only: bool = True                     # спот → шортов нет
    min_trade_notional_usdt: float = 5.0
    max_position_notional_usdt: float = 200.0  # потолок капитала на сделку (тугой стоп → большой размер)

    @classmethod
    def load(cls, path: str = "rules.json") -> "RuleBook":
        if os.path.exists(path):
            with open(path) as f:
                return cls(**json.load(f))
        return cls()


@dataclass
class ComplianceEntry:
    ts: float
    rule: str
    decision: str          # "allow" / "block" / "adjust"
    detail: str


class ComplianceLog:
    """Журнал «правило → действие». Пишется в JSONL для заявки и демо."""

    def __init__(self, path: str = "compliance.jsonl"):
        self.path = path

    def record(self, rule: str, decision: str, detail: str) -> None:
        entry = ComplianceEntry(time.time(), rule, decision, detail)
        with open(self.path, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")


# ── Менеджер риска ────────────────────────────────────────
class RiskManager:
    """
    Привратник входов. Возвращает (allowed, reason, adjusted_risk).
    Управляет circuit breaker по дневной просадке equity.
    """

    def __init__(self, rules: RuleBook, registry: PositionRegistry,
                 compliance: Optional[ComplianceLog] = None,
                 *, starting_equity: float = 0.0):
        self.rules      = rules
        self.registry   = registry
        self.compliance = compliance or ComplianceLog()
        self.day_start_equity = starting_equity
        self._day = _utc_day()
        self._halted = False

    # equity-трекинг для дневной просадки
    def mark_equity(self, equity: float) -> None:
        if _utc_day() != self._day:                 # новый день — сброс
            self._day = _utc_day()
            self.day_start_equity = equity
            self._halted = False
        if self.day_start_equity <= 0:
            self.day_start_equity = equity

    def daily_drawdown_pct(self, equity: float) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return max(0.0, (self.day_start_equity - equity) / self.day_start_equity * 100)

    def circuit_broken(self, equity: float) -> bool:
        if self.daily_drawdown_pct(equity) >= self.rules.max_daily_drawdown_pct:
            if not self._halted:
                self.compliance.record(
                    "max_daily_drawdown_pct", "block",
                    f"DD {self.daily_drawdown_pct(equity):.2f}% ≥ "
                    f"{self.rules.max_daily_drawdown_pct}% → новые входы остановлены")
            self._halted = True
        return self._halted

    def check_entry(self, *, symbol: str, direction: str, equity: float,
                    requested_risk: float) -> Dict:
        """
        Решение по входу. Возвращает {allowed, risk_usdt, reason}.
        risk_usdt — возможно срезанный до cap.
        """
        r = self.rules

        if self.circuit_broken(equity):
            return self._deny("max_daily_drawdown_pct",
                              "дневной circuit breaker активен")

        if r.long_only and direction != "bull":
            return self._deny("long_only", f"{direction}: шорт на споте запрещён")

        if r.blocked_tokens and symbol in r.blocked_tokens:
            return self._deny("blocked_tokens", f"{symbol} в чёрном списке")

        if r.allowed_tokens and symbol not in r.allowed_tokens:
            return self._deny("allowed_tokens", f"{symbol} вне разрешённой вселенной")

        open_n = len(self.registry.open_positions())
        if open_n >= r.max_concurrent_positions:
            return self._deny("max_concurrent_positions",
                              f"{open_n} открытых ≥ лимита {r.max_concurrent_positions}")

        risk = min(requested_risk, r.max_risk_per_trade_usdt)
        if risk < requested_risk:
            self.compliance.record(
                "max_risk_per_trade_usdt", "adjust",
                f"{symbol}: риск срезан {requested_risk}→{risk} USDT")

        self.compliance.record("entry", "allow",
                               f"{symbol} {direction} risk={risk} USDT")
        return {"allowed": True, "risk_usdt": risk, "reason": "ok"}

    def _deny(self, rule: str, detail: str) -> Dict:
        self.compliance.record(rule, "block", detail)
        return {"allowed": False, "risk_usdt": 0.0, "reason": detail}


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())
