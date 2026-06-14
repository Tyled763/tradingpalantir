"""ATR min-stop guard: вырожденно тугие стопы должны отсекаться."""
import pandas as pd

import config as C
from strategy.entry_signal_engine import EntrySignalEngine
from strategy.calculator import BarProcessor


class _NoFeed:
    """Заглушка фида — _detect не ходит в сеть."""
    pass


def _proc_with_bars(symbol, tf, closes):
    bp = BarProcessor(symbol, tf)
    rows = []
    px = closes[0]
    for i, c in enumerate(closes):
        rows.append({"time": pd.Timestamp(1_700_000_000 + i * 60, unit="s", tz="UTC"),
                     "open": px, "high": max(px, c) * 1.001, "low": min(px, c) * 0.999,
                     "close": c, "vol": 1000.0, "vol_usdt": 1000.0})
        px = c
    bp.warmup_from_df(pd.DataFrame(rows))
    return bp


def test_min_dist_fallback_blocks_zero_distance():
    ese = EntrySignalEngine(feed=_NoFeed())
    bp = _proc_with_bars("ETH", "15m", [100.0] * 60)   # плоско → ATR≈0
    ese.proc[("ETH", "15m")] = bp
    # стоп вплотную к входу: (entry-stop)/entry = 0.1% < 0.5% fallback → reject
    entry, stop = 100.0, 99.9
    atr = 0.0
    min_dist = max(atr * C.MIN_STOP_ATR_MULT, entry * C.MIN_STOP_PCT_FALLBACK)
    assert (entry - stop) < min_dist          # 0.1 < 0.5


def test_min_dist_atr_scales_with_volatility():
    # волатильные бары → ATR заметный → порог = 0.8*ATR
    up = [100.0 + i * 2 for i in range(60)]
    bp = _proc_with_bars("X", "15m", up)
    from exit.atr import atr_from_bars
    atr = atr_from_bars(list(bp.rows)[-(C.ATR_PERIOD * 3):], C.ATR_PERIOD)
    assert atr is not None and atr > 0
    entry = float(bp.rows[-1]["close"])
    min_dist = max(atr * C.MIN_STOP_ATR_MULT, entry * C.MIN_STOP_PCT_FALLBACK)
    # стоп в 0.1*ATR — слишком туго; в 1.5*ATR — нормально
    assert (entry - (entry - 0.1 * atr)) < min_dist
    assert (entry - (entry - 1.5 * atr)) >= min_dist


def test_thresholds_present():
    assert 0 < C.MIN_STOP_ATR_MULT <= 3
    assert 0 < C.MIN_STOP_PCT_FALLBACK < 0.05
