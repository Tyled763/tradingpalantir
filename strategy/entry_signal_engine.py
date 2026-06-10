# =========================
# strategy/entry_signal_engine.py — TradingPalantir
# Entry Signal Engine (§14 спека) — ОБЁРТКА проприетарной системы пользователя
# (FVG + VWAP + кросс-ТФ EMA + фрактальный стоп; calculator.py/engine.py —
# портированы без изменений). Взаимозаменяемый модуль: только он решает,
# ГДЕ точка входа. Stage C флоу: мониторит бары ТОЛЬКО armed-монет.
# =========================
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import config as C
from marketdata.gt_feed import GeckoTerminalFeed, warmup_df_for_token
from strategy.calculator import BarProcessor, calc_tp
from strategy.engine import check_all_signals


def _bar_ms(row: Dict) -> int:
    return int(row["time"].timestamp() * 1000)


def _finite(x) -> bool:
    try:
        return x is not None and x == x and abs(float(x)) != float("inf")
    except (TypeError, ValueError):
        return False


class EntrySignalEngine:
    def __init__(self, feed: Optional[GeckoTerminalFeed] = None):
        self.feed = feed or GeckoTerminalFeed("bsc")
        self.proc: Dict[Tuple[str, str], BarProcessor] = {}
        self.last_ts: Dict[Tuple[str, str], int] = {}
        self.next_fetch: Dict[Tuple[str, str], float] = {}
        self._meta: Dict[str, Dict] = {}    # symbol -> {address, pool}

    # ── управление набором мониторинга ────────────────────
    def monitored(self) -> List[str]:
        return list(self._meta.keys())

    async def watch(self, symbol: str, address: str, pool: str) -> bool:
        """Прогревает 4 ТФ для монеты (если ещё не следим)."""
        if symbol in self._meta:
            return True
        ok = True
        for tf in C.TIMEFRAMES:
            try:
                df = await warmup_df_for_token(self.feed, address, tf,
                                               limit=C.WARMUP_BARS, pool=pool)
                bp = BarProcessor(symbol, tf)
                bp.warmup_from_df(df)
                self.proc[(symbol, tf)] = bp
                if bp.rows:
                    self.last_ts[(symbol, tf)] = _bar_ms(bp.rows[-1])
                self.next_fetch[(symbol, tf)] = time.time() + C.TF_TO_MS[tf] / 1000
            except Exception as e:
                ok = False
                print(f"[ESE] warmup {symbol} {tf}: {type(e).__name__}")
        if ok or any((symbol, tf) in self.proc for tf in C.TIMEFRAMES):
            self._meta[symbol] = {"address": address, "pool": pool}
        return ok

    def unwatch(self, symbol: str) -> None:
        self._meta.pop(symbol, None)
        for tf in C.TIMEFRAMES:
            self.proc.pop((symbol, tf), None)
            self.last_ts.pop((symbol, tf), None)
            self.next_fetch.pop((symbol, tf), None)

    def row(self, symbol: str, tf: str) -> Optional[Dict]:
        bp = self.proc.get((symbol, tf))
        return bp.rows[-1] if bp and bp.rows else None

    # ── cadence-инжест + детекция сигналов ────────────────
    async def poll(self) -> List[Dict]:
        """
        Опрашивает «созревшие» (symbol,tf), возвращает список сигналов:
        {symbol, tf, type, direction, entry, stop, tp, row}.
        """
        signals: List[Dict] = []
        now = time.time()
        for (sym, tf), due in list(self.next_fetch.items()):
            if now < due or sym not in self._meta:
                continue
            try:
                rows = await self._ingest(sym, tf)
            except Exception as e:
                print(f"[ESE] {sym} {tf} ingest: {type(e).__name__}")
                self.next_fetch[(sym, tf)] = now + 60
                continue
            for row in rows:
                signals.extend(self._detect(sym, tf, row))
        return signals

    async def _ingest(self, sym: str, tf: str) -> List[Dict]:
        bp = self.proc.get((sym, tf))
        meta = self._meta.get(sym)
        if bp is None or meta is None:
            return []
        bars = await self.feed.bars_for_token(meta["address"], tf, limit=5,
                                              pool=meta["pool"])
        tf_ms = C.TF_TO_MS[tf]
        now_ms = int(time.time() * 1000)
        last = self.last_ts.get((sym, tf), 0)
        added: List[Dict] = []
        for b in bars:
            ts = int(b["time"].timestamp() * 1000)
            if ts <= last or ts + tf_ms > now_ms:    # видели / ещё формируется
                continue
            row = bp.process_bar(b)
            if row is not None:
                added.append(row)
            self.last_ts[(sym, tf)] = ts
        self.next_fetch[(sym, tf)] = now_ms / 1000 + tf_ms / 1000
        return added

    def _detect(self, sym: str, tf: str, row: Dict) -> List[Dict]:
        if not (_finite(row.get("bull_fvg")) or _finite(row.get("bear_fvg"))):
            return []
        bar_ms = _bar_ms(row)
        ema_prev: Dict[str, float] = {}
        for etf in C.TIMEFRAMES:
            bp = self.proc.get((sym, etf))
            v = bp.get_ema_at_cutoff(bar_ms - C.TF_TO_MS[etf]) if bp else None
            if v is None:
                return []
            ema_prev[etf] = v
        out = []
        for sig in check_all_signals(row, ema_prev):
            if sig["direction"] != "bull":          # спот long-only
                continue
            entry = float(row["close"])
            stop = self.proc[(sym, tf)].find_fractal_stop("bull", entry, C.FRACTAL_N)
            if stop >= entry:
                continue
            tp = calc_tp("bull", entry, stop)["tp"]
            out.append({"symbol": sym, "tf": tf, "type": sig["type"],
                        "direction": "bull", "entry": entry, "stop": stop,
                        "tp": tp, "row": row})
        return out
