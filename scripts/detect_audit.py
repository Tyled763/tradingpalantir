# =========================
# scripts/detect_audit.py — TradingPalantir
# Аудит live-детекции: берёт мониторящиеся монеты, мини-реплеит последние
# 5m-бары через ТОТ ЖЕ конвейер (BarProcessor.process_bar → _detect-логика),
# считает бычьи сигналы. Отвечает на вопрос: «баг live-пути или плоский рынок».
#
# Запуск:  python3 -m scripts.detect_audit ETH,BRETT,LTC,XRP,SHIB,SKYAI,IP,PENDLE,LINK,DOT
# (без аргумента — берёт последний ARMED_SET_UPDATED из журнала)
# =========================
from __future__ import annotations

import asyncio
import json
import sys
import warnings
from typing import List

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import pandas as pd

import config as C
from marketdata.gt_feed import GeckoTerminalFeed
from marketdata.token_registry import TokenRegistry
from strategy.calculator import BarProcessor, calc_tp
from strategy.engine import check_all_signals


def _finite(x):
    try:
        return x is not None and x == x and abs(float(x)) != float("inf")
    except (TypeError, ValueError):
        return False


async def audit_symbol(feed, e, replay_n=60):
    """Мини-реплей: warmup до окна, прогон последних replay_n 5m-баров → бычьи сигналы."""
    bars = {}
    for tf in C.TIMEFRAMES:
        lim = 1000 if tf != "5m" else min(replay_n + 400, 1000)
        bars[tf] = await feed.bars(e.pool, tf, limit=lim, token=e.address)
    if len(bars["5m"]) < replay_n + 50:
        return {"symbol": e.symbol, "signals": 0, "bars": len(bars["5m"]), "note": "мало баров"}

    t_start = bars["5m"][-replay_n]["time"]
    proc = {}
    replay_bars = []
    for tf in C.TIMEFRAMES:
        pre = [b for b in bars[tf] if b["time"] < t_start]
        post = [b for b in bars[tf] if b["time"] >= t_start]
        bp = BarProcessor(e.symbol, tf)
        if pre:
            bp.warmup_from_df(pd.DataFrame(pre))
        proc[tf] = bp
        replay_bars += [(b["time"], tf, b) for b in post]
    replay_bars.sort(key=lambda x: (x[0], C.TIMEFRAMES.index(x[1])))

    signals = 0
    fvg_bars = 0
    for t, tf, bar in replay_bars:
        row = proc[tf].process_bar(bar)
        if row is None:
            continue
        if not (_finite(row.get("bull_fvg")) or _finite(row.get("bear_fvg"))):
            continue
        fvg_bars += 1
        bar_ms = int(row["time"].timestamp() * 1000)
        ema_prev = {}
        ok = True
        for etf in C.TIMEFRAMES:
            v = proc[etf].get_ema_at_cutoff(bar_ms - C.TF_TO_MS[etf])
            if v is None:
                ok = False
                break
            ema_prev[etf] = v
        if not ok:
            continue
        for sig in check_all_signals(row, ema_prev):
            if sig["direction"] != "bull":
                continue
            entry = float(row["close"])
            stop = proc[tf].find_fractal_stop("bull", entry, C.FRACTAL_N)
            if stop < entry:
                signals += 1
    return {"symbol": e.symbol, "signals": signals, "fvg_bars": fvg_bars,
            "bars": len(bars["5m"])}


async def main(symbols: List[str]):
    reg = TokenRegistry(C.TOKEN_REGISTRY_FILE).load()
    feed = GeckoTerminalFeed("bsc")
    print(f"аудит live-детекции по {len(symbols)} монетам (реплей последних ~60 5m-баров)\n")
    total = 0
    for sym in symbols:
        e = reg.entries.get(sym)
        if not e or not e.pool:
            print(f"  {sym:8} — нет в реестре/без пула")
            continue
        try:
            r = await audit_symbol(feed, e)
            total += r["signals"]
            print(f"  {r['symbol']:8} bull-сигналов: {r['signals']:2}  "
                  f"(fvg-баров: {r.get('fvg_bars','?')}, всего 5m: {r['bars']})")
        except Exception as ex:
            print(f"  {sym:8} — ошибка: {type(ex).__name__}: {ex}")
    print(f"\nИТОГО bull-сигналов по мониторящемуся набору за ~5ч: {total}")
    print("Вывод: >0 при 0 живых TRADE_SIGNAL → баг live-цикла; 0 → рынок плоский (детекция ОК).")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        syms = sys.argv[1].split(",")
    else:
        import sqlite3
        con = sqlite3.connect(C.JOURNAL_DB)
        row = con.execute("SELECT payload FROM events WHERE event='ARMED_SET_UPDATED' "
                          "ORDER BY ts DESC LIMIT 1").fetchone()
        syms = [a[0] for a in json.loads(row[0])["armed"]] if row else ["ETH"]
    asyncio.run(main(syms))
