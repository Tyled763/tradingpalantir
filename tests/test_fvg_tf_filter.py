"""FVG-вход разрешён только на 30m/1H; 5m/15m — отсекаются.

Мульти-ТФ EMA-confluence не трогаем: все 4 ТФ по-прежнему читаются.
Здесь режется только ТФ, на котором детектится сам входной FVG.
"""
import pandas as pd

import config as C
from strategy.entry_signal_engine import EntrySignalEngine


class _NoFeed:
    """Заглушка фида — _detect не ходит в сеть."""
    pass


class _FakeProc:
    """Процессор с фиксированным EMA и широким фрактальным стопом."""
    def __init__(self, ema, rows):
        self._ema = ema
        self.rows = rows

    def get_ema_at_cutoff(self, cutoff_ms):
        return self._ema

    def find_fractal_stop(self, direction, entry, n):
        return entry * 0.95          # стоп 5% — заведомо проходит ATR-гейт


def _breakout_row():
    """row, дающий BULL breakout (все EMA + VWAP внутри FVG-зоны)."""
    lo, hi = 99.0, 101.0
    return {
        "time": pd.Timestamp(1_700_000_000, unit="s", tz="UTC"),
        "close": 100.0,
        "bull_fvg": 100.0,
        "bear_fvg": float("nan"),
        "fvg_low": lo, "fvg_high": hi,
        "vwap_prev": 100.0,
        "vwap_upper_prev": 100.0,
        "vwap_lower_prev": 100.0,
    }


def _ese_with_procs():
    """ESE с одинаковыми процессорами на всех 4 ТФ → сигнал зависит только от tf входа."""
    ese = EntrySignalEngine(feed=_NoFeed())
    # волатильные бары → ATR > 0; стоп 5% всё равно шире порога
    rows = [{"high": 102.0, "low": 98.0, "close": 100.0} for _ in range(60)]
    for tf in C.TIMEFRAMES:
        ese.proc[("X", tf)] = _FakeProc(ema=100.0, rows=rows)
    return ese


def test_allowed_tf_emits_signal():
    ese = _ese_with_procs()
    out = ese._detect("X", "1H", _breakout_row())
    assert out, "1H FVG должен порождать вход"
    assert out[0]["tf"] == "1H" and out[0]["direction"] == "bull"


def test_excluded_tf_blocked():
    ese = _ese_with_procs()
    # тот же самый сетап — только ТФ другой → должен быть отсечён
    assert ese._detect("X", "5m", _breakout_row()) == []
    assert ese._detect("X", "15m", _breakout_row()) == []


def test_config_lists_only_30m_1h():
    assert C.FVG_ENTRY_TIMEFRAMES == ["30m", "1H"]
    assert "5m" not in C.FVG_ENTRY_TIMEFRAMES
    assert "15m" not in C.FVG_ENTRY_TIMEFRAMES
    # все 4 ТФ остаются в TIMEFRAMES для EMA-confluence
    assert set(C.FVG_ENTRY_TIMEFRAMES).issubset(set(C.TIMEFRAMES))
