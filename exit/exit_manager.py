# =========================
# exit/exit_manager.py — TradingPalantir
# Adaptive Exit Optimizer (§15 спека) + логика пользователя (приоритетна):
#
#   ВХОД без фильтра OscMatrix → ведение в режиме NORMAL (фикс-TP RR3 + стоп).
#   Confluence (mf_bull AND trendflex>0) ПОСЛЕ входа → латч RIDE:
#     TP снимается, держим пока trendflex>0, выход при флипе ≤0.
#   Страховка под ride (из спека, стоп ходит ТОЛЬКО вверх):
#     +1R → стоп=BE · +2R → стоп=entry+1R · ATR-трейл (highest − ATR·mult).
#   SL всегда активен и всегда приоритетен.
#
# evaluate() — чистая функция позиции+баров → действие; исполняет оркестратор.
# =========================
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import config as C
from exit.atr import ATR


@dataclass
class ExitAction:
    kind: str                 # hold | exit | move_stop
    exit_reason: str = ""     # sl | tp | trendflex | emergency
    exit_px: float = 0.0
    new_stop: float = 0.0
    note: str = ""


class ExitManager:
    def __init__(self):
        self._highest: Dict[int, float] = {}     # sid -> max(high) с входа
        self._atr: Dict[int, ATR] = {}           # sid -> ATR на ТФ позиции

    def forget(self, sid: int) -> None:
        self._highest.pop(sid, None)
        self._atr.pop(sid, None)

    def evaluate(self, pos, *, px: float, bar_high: float, bar_low: float,
                 tf_row: Optional[Dict], emergency: bool = False) -> ExitAction:
        """
        pos: Position (entry/stop/tp/ride_mode/sid). px: close базового ТФ.
        tf_row: последний row ТФ позиции (trendflex/mf_bull + high/low/close).
        """
        # 0) emergency flatten (drawdown guard / §8.5)
        if emergency:
            return ExitAction("exit", "emergency", px, note="emergency flatten")

        # 1) SL всегда приоритетен (по low бара)
        if bar_low <= pos.stop:
            return ExitAction("exit", "sl", pos.stop)

        tflex = tf_row.get("trendflex") if tf_row else None
        mf_bull = bool(tf_row.get("mf_bull")) if tf_row else False

        # 2) латч ride-режима (confluence после входа); сам флаг ставит оркестратор
        ride_latch = (C.RIDE_MODE_ENABLED and not pos.ride_mode
                      and mf_bull and tflex is not None and tflex > 0)

        if pos.ride_mode or ride_latch:
            # 3) ride: выход по флипу trendflex
            if tflex is not None and tflex <= 0:
                return ExitAction("exit", "trendflex", px,
                                  note="trendflex flip ≤ 0")
            # 4) страховочная подтяжка стопа (только вверх)
            new_stop = self._protective_stop(pos, bar_high, tf_row)
            if new_stop > pos.stop:
                return ExitAction("move_stop", new_stop=new_stop,
                                  note=("ride latch + stop up" if ride_latch
                                        else "stop up"))
            if ride_latch:
                return ExitAction("hold", note="ride latch")
            return ExitAction("hold")

        # 5) normal-режим: фикс-TP RR3 (по high бара)
        if bar_high >= pos.tp:
            return ExitAction("exit", "tp", pos.tp)
        return ExitAction("hold")

    def _protective_stop(self, pos, bar_high: float, tf_row: Optional[Dict]) -> float:
        """max(R-прогрессия, ATR-трейл); никогда не ниже текущего стопа."""
        risk = pos.entry - pos.stop_initial if hasattr(pos, "stop_initial") else None
        # риск считаем от ОРИГИНАЛЬНОГО стопа; если поля нет — от текущего расстояния tp
        if not risk or risk <= 0:
            risk = (pos.tp - pos.entry) / C.RR_RATIO if pos.tp > pos.entry else None

        hi = self._highest.get(pos.sid, pos.entry)
        hi = max(hi, bar_high)
        self._highest[pos.sid] = hi

        candidates = [pos.stop]
        if risk and risk > 0:
            if hi >= pos.entry + C.R_LOCK_TRIGGER * risk:
                candidates.append(pos.entry + risk)          # +2R → стоп +1R
            elif hi >= pos.entry + C.R_BE_TRIGGER * risk:
                candidates.append(pos.entry)                 # +1R → безубыток
        # ATR-трейл на ТФ позиции
        if tf_row:
            a = self._atr.get(pos.sid)
            if a is None:
                a = self._atr[pos.sid] = ATR(C.ATR_PERIOD)
            v = a.update(float(tf_row.get("high", bar_high)),
                         float(tf_row.get("low", pos.stop)),
                         float(tf_row.get("close", bar_high)))
            if v:
                candidates.append(hi - v * C.ATR_TRAIL_MULT)
        return max(candidates)
