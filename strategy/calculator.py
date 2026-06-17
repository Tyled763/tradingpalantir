# =========================
# calculator.py — Signal Bot v4
# Bar-by-bar: FVG + VWAP (quote vol) + EMA (EMA_PERIOD) + TP
# =========================
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

import numpy as np
import pandas as pd

from config import (
    EMA_PERIOD, MAX_BARS, WARMUP_BARS, RR_RATIO,
    OSC_MF_LEN, OSC_MF_SMOOTH, OSC_TH_LEN, OSC_MF_COMPRESS,
    OSC_MID_LEVEL, OSC_TH_MULT, OSC_HW_LEN, OSC_HW_SMOOTH,
)
from strategy.oscmatrix import OscMatrix


@dataclass
class BarState:
    # FVG
    prev_high:  float = np.nan
    prev_low:   float = np.nan
    prev2_high: float = np.nan
    prev2_low:  float = np.nan
    # VWAP
    sess_date:       Optional[object] = None
    cum_tp_vol:      float = 0.0
    cum_vol_usdt:    float = 0.0
    cum_tp_sq_vol:   float = 0.0
    prev_vwap:       float = np.nan
    prev_vwap_upper: float = np.nan
    prev_vwap_lower: float = np.nan
    # EMA (EMA_PERIOD; prev_ema = EMA последнего обработанного бара)
    prev_ema: float = np.nan
    ema_bars: int   = 0


def _fvg_to_str(x) -> str:
    if x is None:
        return ""
    side, a, b, idx = x
    return f"{side}|{a}|{b}|{idx}"


def _calc_tp(signal: str, entry: float, stop: float) -> Dict:
    risk_points = abs(entry - stop)
    risk_pct    = (risk_points / entry * 100) if entry != 0 else 0.0
    tp_points   = risk_points * RR_RATIO
    tp_pct      = risk_pct * RR_RATIO
    tp          = (entry + tp_points) if signal == "bull" else (entry - tp_points)
    return {
        "tp":          tp,
        "risk_points": risk_points,
        "risk_pct":    risk_pct,
        "tp_points":   tp_points,
        "tp_pct":      tp_pct,
    }


# Публичная обёртка — для пересчёта TP в bot.py при подмене стопа на S/R-уровень.
def calc_tp(signal: str, entry: float, stop: float) -> Dict:
    return _calc_tp(signal, entry, stop)


_NO_TP = {"tp": np.nan, "risk_points": np.nan,
           "risk_pct": np.nan, "tp_points": np.nan, "tp_pct": np.nan}


def _process_bar(bar: Dict, state: BarState, bar_idx: int) -> Dict:
    t      = bar["time"]
    o      = float(bar["open"])
    h      = float(bar["high"])
    l      = float(bar["low"])
    c      = float(bar["close"])
    v      = float(bar["vol"])
    v_usdt = float(bar["vol_usdt"])
    sess   = pd.Timestamp(t).normalize()

    # ── FVG ──────────────────────────────────────────────
    fvg_val  = None
    bull_fvg = np.nan
    bear_fvg = np.nan
    fvg_high = np.nan
    fvg_low  = np.nan

    if np.isfinite(state.prev2_high) and np.isfinite(state.prev2_low):
        if l > state.prev2_high:
            fvg_val  = ("bullish", state.prev2_high, l, bar_idx)
            bull_fvg = l
            fvg_high = state.prev_high
            fvg_low  = state.prev_low
        elif h < state.prev2_low:
            fvg_val  = ("bearish", state.prev2_low, h, bar_idx)
            bear_fvg = h
            fvg_high = state.prev_high
            fvg_low  = state.prev_low

    state.prev2_high = state.prev_high
    state.prev2_low  = state.prev_low
    state.prev_high  = h
    state.prev_low   = l

    # ── VWAP ─────────────────────────────────────────────
    if sess != state.sess_date:
        state.sess_date       = sess
        state.cum_tp_vol      = 0.0
        state.cum_vol_usdt    = 0.0
        state.cum_tp_sq_vol   = 0.0
        state.prev_vwap       = np.nan
        state.prev_vwap_upper = np.nan
        state.prev_vwap_lower = np.nan

    tp_price = (h + l + c) / 3.0
    state.cum_tp_vol    += tp_price * v_usdt
    state.cum_tp_sq_vol += tp_price * tp_price * v_usdt
    state.cum_vol_usdt  += v_usdt

    if state.cum_vol_usdt > 0:
        vwap     = state.cum_tp_vol / state.cum_vol_usdt
        mean_sq  = state.cum_tp_sq_vol / state.cum_vol_usdt
        vwap_std = float(np.sqrt(max(mean_sq - vwap * vwap, 0.0)))
    else:
        vwap     = np.nan
        vwap_std = 0.0

    vwap_upper      = (vwap + vwap_std) if np.isfinite(vwap) else np.nan
    vwap_lower      = (vwap - vwap_std) if np.isfinite(vwap) else np.nan
    vwap_prev       = state.prev_vwap
    vwap_upper_prev = state.prev_vwap_upper
    vwap_lower_prev = state.prev_vwap_lower

    state.prev_vwap       = vwap
    state.prev_vwap_upper = vwap_upper
    state.prev_vwap_lower = vwap_lower

    # ── EMA (EMA_PERIOD; TV-formula: close-init, no SMA) ─────────
    # Точная формула TradingView ta.ema():
    #   EMA[0] = close[0]                          (первая цена)
    #   EMA[t] = α × close[t] + (1-α) × EMA[t-1]   (рекурсия с t=1)
    #   α = 2 / (period + 1)
    #
    # Не используем SMA-инициализацию — она даёт расхождение с TV.
    alpha        = 2.0 / (EMA_PERIOD + 1)
    prev_ema_val = state.prev_ema   # EMA предыдущего бара (для row["ema_prev"])
    state.ema_bars += 1

    if state.ema_bars == 1:
        ema = c                     # первый бар — стартовое значение = close
    else:
        ema = alpha * c + (1.0 - alpha) * state.prev_ema

    state.prev_ema = ema            # сохраняем для следующего бара

    # ── TP ───────────────────────────────────────────────
    if np.isfinite(bull_fvg) and np.isfinite(fvg_low):
        tp_data = _calc_tp("bull", float(bull_fvg), float(fvg_low))
    elif np.isfinite(bear_fvg) and np.isfinite(fvg_high):
        tp_data = _calc_tp("bear", float(bear_fvg), float(fvg_high))
    else:
        tp_data = _NO_TP

    return {
        "time":            t,
        "open":            o,
        "high":            h,
        "low":             l,
        "close":           c,
        "vol":             v,
        "vol_usdt":        v_usdt,
        # FVG
        "FVG":             _fvg_to_str(fvg_val),
        "bull_fvg":        bull_fvg,
        "bear_fvg":        bear_fvg,
        "fvg_high":        fvg_high,
        "fvg_low":         fvg_low,
        # VWAP
        "vwap":            vwap,
        "vwap_upper":      vwap_upper,
        "vwap_lower":      vwap_lower,
        "vwap_prev":       vwap_prev,
        "vwap_upper_prev": vwap_upper_prev,
        "vwap_lower_prev": vwap_lower_prev,
        # EMA
        "ema":             ema,
        "ema_prev":        prev_ema_val,
        # TP
        **tp_data,
    }


class BarProcessor:
    def __init__(self, symbol: str, timeframe: str):
        self.symbol    = symbol
        self.timeframe = timeframe
        self.state     = BarState()
        self.rows: Deque[Dict] = deque(maxlen=MAX_BARS)
        self.warmed_up = False
        self._bar_idx  = 0
        # кастомный индикатор пользователя (Money Flow + Trendflex)
        self.osc = OscMatrix(
            mf_len=OSC_MF_LEN, mf_smooth=OSC_MF_SMOOTH, th_len=OSC_TH_LEN,
            mf_compress=OSC_MF_COMPRESS, mid_level=OSC_MID_LEVEL, th_mult=OSC_TH_MULT,
            hw_len=OSC_HW_LEN, hw_smooth=OSC_HW_SMOOTH,
        )

    def _osc_merge(self, row: Dict) -> None:
        """Считает OscMatrix на баре и кладёт значения в row (вызывать для КАЖДОГО бара по порядку)."""
        row.update(self.osc.update(row["high"], row["low"], row["close"], row["vol_usdt"]))

    def warmup_from_df(self, df: pd.DataFrame) -> None:
        if df.empty:
            print(f"[WARMUP] {self.symbol} {self.timeframe}: пустой DataFrame")
            self.warmed_up = True
            return

        total_bars = len(df)
        store_from = max(0, total_bars - WARMUP_BARS)

        print(f"[WARMUP] {self.symbol} {self.timeframe}: "
              f"EMA по {total_bars} барам, rows — последние {total_bars - store_from}...")

        # itertuples() в 5–10× быстрее iterrows() — не создаёт Series объект
        for i, row in enumerate(df.itertuples(index=False)):
            bar = row._asdict()
            result = _process_bar(bar, self.state, self._bar_idx)
            self._osc_merge(result)          # OscMatrix для каждого бара (стейт непрерывен)
            # В rows кладём только последние WARMUP_BARS баров
            if i >= store_from:
                self.rows.append(result)
            self._bar_idx += 1

        self.warmed_up = True
        print(f"[WARMUP] {self.symbol} {self.timeframe}: готов ✓  "
              f"(rows: {len(self.rows)})")

    def get_ema_at_cutoff(self, cutoff_ms: int) -> Optional[float]:
        """
        Возвращает EMA из последнего бара где open_time <= cutoff_ms.
        Используется для исторически корректного EMA при FVG на старшем ТФ:
          cutoff = bar_open_ms − TF_ind_ms
        Поиск с конца deque — обычно находит за 1–2 итерации.
        """
        # reversed() работает напрямую с deque — не копирует в list
        for row in reversed(self.rows):
            row_ms = int(row["time"].timestamp() * 1000)
            if row_ms <= cutoff_ms:
                return float(row["ema"])
        return None

    def find_fractal_stop(self, direction: str, entry: float, n: int = 1) -> float:
        """Ближайший фрактальный экстремум СТРОГО за входом → цена стопа.

        bull → fractal low < entry (стоп под входом),
        bear → fractal high > entry (стоп над входом).

        Фрактал (Williams, N): центр строго экстремальнее n соседей слева и справа.
        Сигнальная свеча (последний бар) проверяется только по левой стороне.
        Поиск идёт от свежих баров к старым — берём первый подходящий.
        Fallback (фрактала за входом нет) — экстремум всего окна: стоп есть всегда.
        """
        rows = self.rows
        if not rows:
            return float(entry)

        last  = len(rows) - 1
        highs = [r["high"] for r in rows]
        lows  = [r["low"]  for r in rows]

        if direction == "bull":
            # 1) сигнальная свеча — left-only
            if (last >= n
                    and all(lows[last] < lows[last - k] for k in range(1, n + 1))
                    and lows[last] < entry):
                return float(lows[last])
            # 2) двусторонние фракталы, самый свежий первым
            for i in range(last - n, n - 1, -1):
                if (all(lows[i] < lows[i - k] and lows[i] < lows[i + k]
                        for k in range(1, n + 1))
                        and lows[i] < entry):
                    return float(lows[i])
            return float(min(lows))          # fallback

        # bear
        if (last >= n
                and all(highs[last] > highs[last - k] for k in range(1, n + 1))
                and highs[last] > entry):
            return float(highs[last])
        for i in range(last - n, n - 1, -1):
            if (all(highs[i] > highs[i - k] and highs[i] > highs[i + k]
                    for k in range(1, n + 1))
                    and highs[i] > entry):
                return float(highs[i])
        return float(max(highs))             # fallback

    def process_bar(self, bar: Dict) -> Optional[Dict]:
        if not self.warmed_up:
            return None
        row = _process_bar(bar, self.state, self._bar_idx)
        self._osc_merge(row)                 # OscMatrix: money_flow + trendflex в row
        self._bar_idx += 1
        self.rows.append(row)
        return row
