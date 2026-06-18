"""MFI-гейт подтверждения входа: long только при подтверждённом притоке.

Тот же FakeProc/breakout-паттерн, что в test_fvg_tf_filter, но варьируем
OscMatrix-поля в signal-row (mf_bull / mf_raw / money_flow).
"""
import pandas as pd

import config as C
from strategy.entry_signal_engine import EntrySignalEngine


class _NoFeed:
    pass


class _FakeProc:
    def __init__(self, ema, rows):
        self._ema, self.rows = ema, rows

    def get_ema_at_cutoff(self, cutoff_ms):
        return self._ema

    def find_fractal_stop(self, direction, entry, n):
        return entry * 0.95          # широкий стоп — проходит ATR-гейт


def _ese():
    ese = EntrySignalEngine(feed=_NoFeed())
    rows = [{"high": 102.0, "low": 98.0, "close": 100.0} for _ in range(60)]
    for tf in C.TIMEFRAMES:
        ese.proc[("X", tf)] = _FakeProc(ema=100.0, rows=rows)
    return ese


def _row(**mfi):
    lo, hi = 99.0, 101.0
    r = {
        "time": pd.Timestamp(1_700_000_000, unit="s", tz="UTC"),
        "close": 100.0, "bull_fvg": 100.0, "bear_fvg": float("nan"),
        "fvg_low": lo, "fvg_high": hi,
        "vwap_prev": 100.0, "vwap_upper_prev": 100.0, "vwap_lower_prev": 100.0,
        "mf_bull": True, "money_flow": 45.0, "mf_raw": 55.0, "mf_up_th": 60.0,
    }
    r.update(mfi)
    return r


def test_gate_blocks_when_not_bull(monkeypatch):
    monkeypatch.setattr(C, "MFI_ENTRY_GATE", True)
    monkeypatch.setattr(C, "MFI_GATE_MODE", "mf_bull")
    assert _ese()._detect("X", "1H", _row(mf_bull=False)) == []


def test_gate_passes_when_bull(monkeypatch):
    monkeypatch.setattr(C, "MFI_ENTRY_GATE", True)
    monkeypatch.setattr(C, "MFI_GATE_MODE", "mf_bull")
    out = _ese()._detect("X", "1H", _row(mf_bull=True))
    assert out and out[0]["direction"] == "bull"


def test_gate_disabled_passes_regardless(monkeypatch):
    monkeypatch.setattr(C, "MFI_ENTRY_GATE", False)
    assert _ese()._detect("X", "1H", _row(mf_bull=False))    # выключен → пускает


def test_mf_raw_mode_threshold(monkeypatch):
    monkeypatch.setattr(C, "MFI_ENTRY_GATE", True)
    monkeypatch.setattr(C, "MFI_GATE_MODE", "mf_raw_50")
    monkeypatch.setattr(C, "MFI_RAW_MIN", 50.0)
    assert _ese()._detect("X", "1H", _row(mf_raw=49.0)) == []   # ниже порога → режет
    assert _ese()._detect("X", "1H", _row(mf_raw=51.0))         # выше → пускает
