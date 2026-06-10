# =========================
# oscmatrix.py — BNB HACK Trading Agent
# Кастомный индикатор пользователя «Oscillator Matrix» (порт с Pine v6).
# Считаются ДВЕ интересующие величины, bar-by-bar (рекурсивно, как EMA/VWAP):
#
#   Money Flow:  MFI(14) → mfRaw=EMA(4) → mfCenter=EMA(40+(mfRaw−50)·0.65, 4)
#                mf_bull = mfCenter ≥ 40 ; пороги upTh/dnTh = SMA50 ± stdev50·1.0
#   Trendflex:   Ehlers SuperSmoother(close, hwLen=20) → Trendflex (откл. от
#                сглаженного) → RMS-нормализация → hwFast (центр 0)
#                hwSlow = EMA(hwFast, 3) ; hw_bull = hwFast ≥ 0
#
# Точное соответствие Pine: ta.ema (seed=первое значение), ta.sma/ta.stdev
# (population), var-инициализация SuperSmoother/RMS = 0, nz() = 0.
# =========================
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional


class _Ema:
    """Pine ta.ema: первое значение = source, далее alpha·x + (1−alpha)·prev."""
    def __init__(self, length: int):
        self.alpha = 2.0 / (length + 1)
        self.val: Optional[float] = None

    def update(self, x: float) -> float:
        self.val = x if self.val is None else self.alpha * x + (1.0 - self.alpha) * self.val
        return self.val


class OscMatrix:
    def __init__(self, *, mf_len: int = 21, mf_smooth: int = 4, th_len: int = 50,
                 mf_compress: float = 0.65, mid_level: float = 40.0, th_mult: float = 1.0,
                 hw_len: int = 20, hw_smooth: int = 3):
        # параметры
        self.mf_len = mf_len
        self.th_len = th_len
        self.mf_compress = mf_compress
        self.mid = mid_level
        self.th_mult = th_mult
        self.hw_len = hw_len

        # ── Money Flow state ──
        self._tp_prev: Optional[float] = None
        self._up = deque(maxlen=mf_len)          # rolling amt при росте tp
        self._dn = deque(maxlen=mf_len)          # rolling amt при падении tp
        self._mfi_hist = deque(maxlen=th_len)    # для SMA/stdev(50)
        self._ema_mfraw = _Ema(mf_smooth)
        self._ema_mfcenter = _Ema(4)

        # ── Trendflex (SuperSmoother) state ──
        sp = math.sqrt(2.0) * math.pi
        decay = math.exp(-sp / (0.5 * hw_len))
        freq = math.cos(sp / (0.5 * hw_len))
        self._c1 = 2.0 * decay * freq
        self._c2 = -decay * decay
        self._ci = (1.0 - self._c1 - self._c2) / 2.0
        self._close_prev = 0.0                   # nz(close[1]) init 0
        self._ssf_hist = deque(maxlen=hw_len + 1)  # прошлые ssf: [-1]=ssf[1] … [-(hw_len+1)]=ssf[hwLen+1]
        self._sum_ssf = 0.0                      # var float, init 0
        self._rms = 0.0                          # var float, init 0
        self._ema_hwslow = _Ema(hw_smooth)

    def update(self, high: float, low: float, close: float, vol: float) -> Dict:
        # ── Money Flow ──
        tp = (high + low + close) / 3.0
        amt = tp * (vol if vol and vol > 0 else 1.0)
        if self._tp_prev is None:
            up = dn = 0.0                        # ta.change на первом баре = na
        else:
            ch = tp - self._tp_prev
            up = amt if ch > 0 else 0.0
            dn = amt if ch < 0 else 0.0
        self._tp_prev = tp
        self._up.append(up)
        self._dn.append(dn)
        pos, neg = sum(self._up), sum(self._dn)
        mfi = 100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / neg)

        self._mfi_hist.append(mfi)
        mf_raw = self._ema_mfraw.update(mfi)
        n = len(self._mfi_hist)
        mean = sum(self._mfi_hist) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in self._mfi_hist) / n)  # population
        mf_ref = mean
        mf_dev = std * self.th_mult
        mf_center = self._ema_mfcenter.update(self.mid + (mf_raw - 50.0) * self.mf_compress)
        mf_bull = mf_center >= self.mid

        # ── Trendflex ──
        ssf1 = self._ssf_hist[-1] if len(self._ssf_hist) >= 1 else 0.0           # _ssf[1]
        ssf2 = self._ssf_hist[-2] if len(self._ssf_hist) >= 2 else 0.0           # _ssf[2]
        ssf = self._ci * (close + self._close_prev) + self._c1 * ssf1 + self._c2 * ssf2
        ssf_hw1 = (self._ssf_hist[-(self.hw_len + 1)]
                   if len(self._ssf_hist) >= self.hw_len + 1 else 0.0)            # _ssf[hwLen+1]
        self._sum_ssf = self._sum_ssf + ssf1 - ssf_hw1
        avg_dev = (self.hw_len * ssf - self._sum_ssf) / self.hw_len
        self._rms = 0.04 * avg_dev * avg_dev + 0.96 * self._rms
        hw_fast = avg_dev / math.sqrt(self._rms) if self._rms > 0 else 0.0
        self._ssf_hist.append(ssf)
        self._close_prev = close
        hw_slow = self._ema_hwslow.update(hw_fast)
        hw_bull = hw_fast >= 0.0

        return {
            "money_flow": mf_center, "mf_raw": mf_raw, "mf_bull": mf_bull,
            "mf_up_th": mf_ref + mf_dev, "mf_dn_th": mf_ref - mf_dev,
            "trendflex": hw_fast, "trendflex_slow": hw_slow, "hw_bull": hw_bull,
        }
