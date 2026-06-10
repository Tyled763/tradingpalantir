# =========================
# engine.py — Signal Bot v4
# 3 типа сетапов × 2 стороны = 6 сигналов
#
# Сетапы:
#   - Trend     (продолжение тренда)
#   - Breakout  (все EMA внутри FVG)
#   - Reversal  (разворот тренда, строгая цепочка EMA)
# =========================
from __future__ import annotations
import numpy as np
from typing import Dict, Optional, List


# ══════════════════════════════════════════════════════════
# ОБЩИЕ ПРОВЕРКИ
# ══════════════════════════════════════════════════════════
def _inside(val, lo: float, hi: float) -> bool:
    """True если значение конечное и попадает в [lo, hi]."""
    return val is not None and np.isfinite(val) and lo <= val <= hi


def _has_bull_fvg(row: Dict) -> bool:
    return np.isfinite(row.get("bull_fvg", np.nan))


def _has_bear_fvg(row: Dict) -> bool:
    return np.isfinite(row.get("bear_fvg", np.nan))


def _common_checks_bull(row: Dict, ema_5m_prev: float) -> bool:
    """
    Общие условия для BULL Trend и BULL Reversal:
      - bull_fvg есть
      - vwap_prev, ema_5m_prev, vwap_upper_prev ∈ [fvg_low, fvg_high]
    """
    if not _has_bull_fvg(row):
        return False

    lo = row.get("fvg_low")
    hi = row.get("fvg_high")
    if lo is None or hi is None or not (np.isfinite(lo) and np.isfinite(hi)):
        return False

    return (
        _inside(row.get("vwap_prev"),       lo, hi)
        and _inside(ema_5m_prev,            lo, hi)
        and _inside(row.get("vwap_upper_prev"), lo, hi)
    )


def _common_checks_bear(row: Dict, ema_5m_prev: float) -> bool:
    """
    Общие условия для BEAR Trend и BEAR Reversal:
      - bear_fvg есть
      - vwap_prev, ema_5m_prev, vwap_lower_prev ∈ [fvg_low, fvg_high]
    """
    if not _has_bear_fvg(row):
        return False

    lo = row.get("fvg_low")
    hi = row.get("fvg_high")
    if lo is None or hi is None or not (np.isfinite(lo) and np.isfinite(hi)):
        return False

    return (
        _inside(row.get("vwap_prev"),       lo, hi)
        and _inside(ema_5m_prev,            lo, hi)
        and _inside(row.get("vwap_lower_prev"), lo, hi)
    )


# ══════════════════════════════════════════════════════════
# 1. TREND — продолжение тренда
# ══════════════════════════════════════════════════════════
def check_trend(row: Dict, ema_prev: Dict[str, float]) -> Optional[str]:
    """
    BULL: ema_5m_prev > ema_15m_prev, ema_30m_prev, ema_1h_prev
          (между 15m/30m/1h не упорядочиваются)
          + общие проверки
    BEAR: ema_5m_prev < ema_15m_prev, ema_30m_prev, ema_1h_prev
          + общие проверки
    """
    e5  = ema_prev["5m"]
    e15 = ema_prev["15m"]
    e30 = ema_prev["30m"]
    e1h = ema_prev["1H"]

    # BULL
    if e5 > e15 and e5 > e30 and e5 > e1h:
        if _common_checks_bull(row, e5):
            return "bull"

    # BEAR
    if e5 < e15 and e5 < e30 and e5 < e1h:
        if _common_checks_bear(row, e5):
            return "bear"

    return None


# ══════════════════════════════════════════════════════════
# 2. BREAKOUT — все 4 EMA внутри FVG
# ══════════════════════════════════════════════════════════
def check_breakout(row: Dict, ema_prev: Dict[str, float]) -> Optional[str]:
    """
    BULL: bull_fvg + vwap_prev, ema_5m/15m/30m/1h_prev, vwap_upper_prev
          все внутри [fvg_low, fvg_high]
    BEAR: bear_fvg + vwap_prev, ema_5m/15m/30m/1h_prev, vwap_lower_prev
          все внутри [fvg_low, fvg_high]
    """
    lo = row.get("fvg_low")
    hi = row.get("fvg_high")
    if lo is None or hi is None or not (np.isfinite(lo) and np.isfinite(hi)):
        return None

    # Все 4 EMA должны быть внутри
    if not all(_inside(ema_prev[tf], lo, hi) for tf in ("5m", "15m", "30m", "1H")):
        return None

    # BULL
    if (_has_bull_fvg(row)
            and _inside(row.get("vwap_prev"),       lo, hi)
            and _inside(row.get("vwap_upper_prev"), lo, hi)):
        return "bull"

    # BEAR
    if (_has_bear_fvg(row)
            and _inside(row.get("vwap_prev"),       lo, hi)
            and _inside(row.get("vwap_lower_prev"), lo, hi)):
        return "bear"

    return None

# ══════════════════════════════════════════════════════════
# 3. REVERSAL — строгая цепочка EMA
# ══════════════════════════════════════════════════════════
def check_reversal(row: Dict, ema_prev: Dict[str, float]) -> Optional[str]:
    """
    BULL: ema_5m_prev < ema_15m_prev < ema_30m_prev < ema_1h_prev
          + общие проверки (bull_fvg, vwap_prev, ema_5m_prev, vwap_upper_prev в FVG)
    BEAR: ema_5m_prev > ema_15m_prev > ema_30m_prev > ema_1h_prev
          + общие проверки (bear_fvg, vwap_prev, ema_5m_prev, vwap_lower_prev в FVG)
    """
    e5  = ema_prev["5m"]
    e15 = ema_prev["15m"]
    e30 = ema_prev["30m"]
    e1h = ema_prev["1H"]

    # BULL — строгая цепочка вверх
    if e5 < e15 < e30 < e1h:
        if _common_checks_bull(row, e5):
            return "bull"

    # BEAR — строгая цепочка вниз
    if e5 > e15 > e30 > e1h:
        if _common_checks_bear(row, e5):
            return "bear"

    return None

# ══════════════════════════════════════════════════════════
# AGGREGATE — проверка всех 3 типов
# ══════════════════════════════════════════════════════════
def check_all_signals(
    row: Dict,
    ema_prev: Dict[str, float],
) -> List[Dict]:
    """
    Прогоняет row через 3 проверки.
    Возвращает список найденных сигналов.

    Каждый сигнал = dict:
      {
        "type":      "trend" / "breakout" / "reversal",
        "direction": "bull"  / "bear",
      }
    """
    results = []

    for setup_type, check_fn in (
        ("trend",    check_trend),
        ("breakout", check_breakout),
        ("reversal", check_reversal),
    ):
        direction = check_fn(row, ema_prev)
        if direction:
            results.append({"type": setup_type, "direction": direction})

    return results
