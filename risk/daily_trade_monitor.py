# =========================
# risk/daily_trade_monitor.py — TradingPalantir
# Требование хакатона (§2/§19 спека): минимум 1 сделка/день в live-окне.
# Если к FALLBACK_WINDOW_H часам до конца дня UTC сделок 0 — предлагает
# fallback-сделку Tier-1 минимального размера (со стопом, через все гейты).
# НИКОГДА не в обход риск-контролей: при block/flatten fallback запрещён.
# =========================
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, Optional

import config as C
from journal import event_types as ET
from journal.trade_journal import TradeJournal


def _day_start_utc(ts: Optional[float] = None) -> float:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def in_live_window(ts: Optional[float] = None) -> bool:
    d = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).date().isoformat()
    return C.LIVE_WINDOW[0] <= d <= C.LIVE_WINDOW[1]


class DailyTradeMonitor:
    def __init__(self, journal: TradeJournal):
        self.journal = journal

    def trades_today(self) -> int:
        return self.journal.count_since(ET.ORDER_FILLED, _day_start_utc())

    def seconds_left_today(self) -> float:
        return _day_start_utc() + 86400 - time.time()

    def check(self, guard_mode: str) -> Dict:
        """
        {satisfied, trades_today, needs_fallback, reason}
        needs_fallback=True → оркестратор предлагает Tier-1 fallback-сделку
        (она всё равно идёт через Risk Governor).
        """
        trades = self.trades_today()
        live = in_live_window()
        res = {"satisfied": trades >= C.DAILY_MIN_TRADES, "trades_today": trades,
               "live_window": live, "needs_fallback": False, "reason": ""}
        if not live or res["satisfied"]:
            return res
        if self.seconds_left_today() > C.FALLBACK_WINDOW_H * 3600:
            res["reason"] = "ещё рано для fallback"
            return res
        if guard_mode not in ("normal", "defensive"):
            res["reason"] = f"fallback запрещён: guard={guard_mode}"
            self.journal.log(ET.DAILY_TRADE_CHECK, satisfied=False,
                             fallback_blocked=guard_mode)
            return res
        res["needs_fallback"] = True
        res["reason"] = (f"0 сделок сегодня, осталось "
                         f"{self.seconds_left_today()/3600:.1f}ч — нужен fallback Tier-1")
        self.journal.log(ET.DAILY_TRADE_CHECK, satisfied=False, needs_fallback=True)
        return res

    def fallback_candidate(self, watchlist) -> Optional[Dict]:
        """Первый Tier-1 токен из watchlist (минимальный размер, со стопом)."""
        tier1 = [t for t in watchlist if t.symbol in C.FALLBACK_TIER1
                 and t.firewall in ("approved", "skipped")]
        if not tier1:
            return None
        t = tier1[0]
        return {"symbol": t.symbol, "address": t.address, "pool": t.pool,
                "notional": C.FALLBACK_NOTIONAL, "kind": "fallback"}
