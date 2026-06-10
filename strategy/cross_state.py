# =========================
# cross_state.py
# Общее состояние индикаторов со всех ТФ для каждого символа.
# Используется для проверки сигналов которые сравнивают
# EMA / VWAP с разных таймфреймов.
#
# Примечание v4: CrossTimeframeState обновляется после каждого бара,
# но для EMA в сигналах используется get_ema_at_cutoff() из BarProcessor.rows.
# =========================
from __future__ import annotations

from typing import Dict, Optional
import numpy as np


class CrossTimeframeState:
    """
    Структура:
        _state[symbol][tf] = {
            "ema_curr":        float,
            "ema_prev":        float,
            "vwap_curr":       float,
            "vwap_prev":       float,
            "vwap_upper_curr": float,
            "vwap_upper_prev": float,
            "vwap_lower_curr": float,
            "vwap_lower_prev": float,
            "last_bar_time":   pd.Timestamp,
        }

    После каждого закрытого бара bot.py вызывает update().
    """

    def __init__(self):
        self._state: Dict[str, Dict[str, Dict]] = {}

    # ── Обновление после закрытия бара ────────────────────
    def update(self, symbol: str, tf: str, row: Dict) -> None:
        """
        Сохраняет текущие значения индикаторов из row.
        prev значения берутся из row["ema_prev"], row["vwap_prev"] и т.д.
        """
        if symbol not in self._state:
            self._state[symbol] = {}

        self._state[symbol][tf] = {
            "ema_curr":        row.get("ema",        np.nan),
            "ema_prev":        row.get("ema_prev",   np.nan),
            "vwap_curr":       row.get("vwap",       np.nan),
            "vwap_prev":       row.get("vwap_prev",  np.nan),
            "vwap_upper_curr": row.get("vwap_upper", np.nan),
            "vwap_upper_prev": row.get("vwap_upper_prev", np.nan),
            "vwap_lower_curr": row.get("vwap_lower", np.nan),
            "vwap_lower_prev": row.get("vwap_lower_prev", np.nan),
            "last_bar_time":   row.get("time"),
        }
