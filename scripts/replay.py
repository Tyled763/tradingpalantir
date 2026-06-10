# =========================
# replay.py — BNB HACK Trading Agent
# Исторический реплей стратегии на одной монете (paper, без Claude/новостей —
# их нет ретроспективно). Прогоняет бары через ТОТ ЖЕ конвейер, что и agent.py:
# BarProcessor (FVG/VWAP/EMA + OscMatrix) → check_all_signals → фрактальный стоп
# → calc_tp → ведение: normal TP/SL → confluence латчит ride → флип trendflex≤0.
#
# Запуск:  python3 replay.py ETH [5m_bars=900]
# =========================
from __future__ import annotations

import asyncio
import sys
import warnings
from typing import Dict, List, Optional

warnings.filterwarnings("ignore")

import pandas as pd

import sys; sys.path.insert(0, ".")
import config as C
from marketdata.gt_feed import GeckoTerminalFeed
from strategy.calculator import BarProcessor, calc_tp
from strategy.engine import check_all_signals
from marketdata.token_registry import TokenRegistry


class PaperPos:
    def __init__(self, sid, tf, setup, t, entry, stop, tp, qty):
        self.sid, self.tf, self.setup = sid, tf, setup
        self.t_in, self.entry, self.stop, self.tp, self.qty = t, entry, stop, tp, qty
        self.ride = False
        self.t_out = None
        self.exit_px = None
        self.kind = None

    def close(self, t, px, kind):
        self.t_out, self.exit_px, self.kind = t, px, kind

    @property
    def pnl(self):
        if self.exit_px is None:
            return 0.0
        fee = C.ROUNDTRIP_FEE * (self.entry + self.exit_px) * self.qty
        return (self.exit_px - self.entry) * self.qty - fee


async def replay(symbol: str, n5m: int = 900) -> None:
    reg = TokenRegistry(C.TOKEN_REGISTRY_FILE).load()
    e = reg.entries.get(symbol)
    if not e:
        print(f"{symbol} нет в реестре"); return
    feed = GeckoTerminalFeed("bsc")

    print(f"[REPLAY] {symbol} pool={e.pool_name} | качаю историю...")
    bars: Dict[str, List[Dict]] = {}
    for tf in C.TIMEFRAMES:
        lim = 1000 if tf != "5m" else min(n5m + 100, 1000)
        bars[tf] = await feed.bars(e.pool, tf, limit=lim, token=e.address)
        print(f"  {tf}: {len(bars[tf])} баров "
              f"({bars[tf][0]['time']:%m-%d %H:%M} → {bars[tf][-1]['time']:%m-%d %H:%M})")

    # окно реплея = последние (n5m - warmup5m) 5m-баров
    warm5 = max(300, len(bars["5m"]) - n5m + 300)
    t_start = bars["5m"][warm5]["time"]

    # прогрев каждого ТФ до t_start, остаток — в реплей
    proc: Dict[str, BarProcessor] = {}
    replay_bars: List[tuple] = []           # (time, tf, bar)
    for tf in C.TIMEFRAMES:
        pre = [b for b in bars[tf] if b["time"] < t_start]
        post = [b for b in bars[tf] if b["time"] >= t_start]
        bp = BarProcessor(symbol, tf)
        if pre:
            bp.warmup_from_df(pd.DataFrame(pre))
        proc[tf] = bp
        replay_bars += [(b["time"], tf, b) for b in post]
    replay_bars.sort(key=lambda x: (x[0], C.TIMEFRAMES.index(x[1])))
    print(f"[REPLAY] окно: {t_start:%m-%d %H:%M} → {bars['5m'][-1]['time']:%m-%d %H:%M} "
          f"({len([1 for _, tf, _ in replay_bars if tf=='5m'])} 5m-баров)\n")

    positions: List[PaperPos] = []
    closed: List[PaperPos] = []
    sid = 0
    tf_ms = C.TF_TO_MS

    def open_count():
        return len(positions)

    for t, tf, bar in replay_bars:
        row = proc[tf].process_bar(bar)
        if row is None:
            continue
        bar_ms = int(row["time"].timestamp() * 1000)

        # ── ведение позиций на каждом 5m-баре ──
        if tf == "5m":
            px = float(row["close"])
            for p in positions[:]:
                orow = proc[p.tf].rows[-1] if proc[p.tf].rows else {}
                tflex, mfb = orow.get("trendflex"), orow.get("mf_bull")
                if (C.RIDE_MODE_ENABLED and not p.ride and mfb
                        and tflex is not None and tflex > 0):
                    p.ride = True
                lo = float(row["low"])
                if lo <= p.stop:
                    p.close(t, p.stop, "sl")
                elif p.ride:
                    if tflex is not None and tflex <= 0:
                        p.close(t, px, "trendflex")
                elif float(row["high"]) >= p.tp:
                    p.close(t, p.tp, "tp")
                if p.kind:
                    positions.remove(p)
                    closed.append(p)

        # ── сигналы ──
        has_fvg = (row.get("bull_fvg") == row.get("bull_fvg") and row.get("bull_fvg") is not None) or \
                  (row.get("bear_fvg") == row.get("bear_fvg") and row.get("bear_fvg") is not None)
        if not has_fvg:
            continue
        ema_prev = {}
        ok = True
        for etf in C.TIMEFRAMES:
            v = proc[etf].get_ema_at_cutoff(bar_ms - tf_ms[etf])
            if v is None:
                ok = False; break
            ema_prev[etf] = v
        if not ok:
            continue
        for sig in check_all_signals(row, ema_prev):
            if sig["direction"] != "bull" or open_count() >= 2:
                continue
            entry = float(row["close"])
            stop = proc[tf].find_fractal_stop("bull", entry, C.FRACTAL_N)
            if stop >= entry:
                continue
            tp = calc_tp("bull", entry, stop)["tp"]
            qty = C.RISK_USDT / max(entry - stop + C.ROUNDTRIP_FEE * (entry + stop), 1e-12)
            cap = 45.0
            if entry * qty > cap:
                qty = cap / entry
            sid += 1
            positions.append(PaperPos(sid, tf, sig["type"], t, entry, stop, tp, qty))
            print(f"  ▶ #{sid} {t:%m-%d %H:%M} ENTER {sig['type']:9} tf={tf:>3} "
                  f"entry={entry:.2f} stop={stop:.2f} tp={tp:.2f}")

    # незакрытые — по последней цене
    last_px = float(proc["5m"].rows[-1]["close"])
    for p in positions:
        p.close(replay_bars[-1][0], last_px, "eod")
        closed.append(p)

    # ── отчёт ──
    print()
    if not closed:
        print("Сделок не было (сигналы не сработали в окне).")
        return
    total = 0.0
    for p in closed:
        total += p.pnl
        ride = "RIDE" if p.ride else "    "
        print(f"  ✕ #{p.sid} {p.t_in:%m-%d %H:%M}→{p.t_out:%m-%d %H:%M} {p.setup:9} tf={p.tf:>3} "
              f"{ride} exit={p.kind:9} entry={p.entry:.2f} out={p.exit_px:.2f} pnl={p.pnl:+.3f}")
    wins = [p for p in closed if p.pnl > 0]
    print(f"\nИтог: {len(closed)} сделок | win {len(wins)}/{len(closed)} | PnL {total:+.3f} USDT "
          f"(риск {C.RISK_USDT}/сделку, кап $45)")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "ETH"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    asyncio.run(replay(sym, n))
