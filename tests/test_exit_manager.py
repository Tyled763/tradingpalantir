from dataclasses import dataclass, field

from exit.exit_manager import ExitManager


@dataclass
class P:
    sid: int = 1
    entry: float = 100.0
    stop: float = 98.0
    tp: float = 106.0          # RR 3.0
    ride_mode: bool = False


def row(trendflex=0.5, mf_bull=True, high=101.0, low=99.0, close=100.5):
    return {"trendflex": trendflex, "mf_bull": mf_bull,
            "high": high, "low": low, "close": close}


def test_sl_always_first():
    em = ExitManager()
    a = em.evaluate(P(ride_mode=True), px=97.0, bar_high=99.0, bar_low=97.0,
                    tf_row=row(trendflex=0.9))
    assert a.kind == "exit" and a.exit_reason == "sl" and a.exit_px == 98.0


def test_normal_mode_tp_hit():
    em = ExitManager()
    a = em.evaluate(P(), px=106.5, bar_high=106.5, bar_low=105.0,
                    tf_row=row(trendflex=-0.5, mf_bull=False))  # без confluence
    assert a.kind == "exit" and a.exit_reason == "tp" and a.exit_px == 106.0


def test_ride_latch_on_confluence_ignores_tp():
    em = ExitManager()
    # confluence есть → латч ride; цена выше TP, но держим
    a = em.evaluate(P(), px=107.0, bar_high=107.0, bar_low=106.0,
                    tf_row=row(trendflex=0.8, mf_bull=True))
    assert a.kind in ("hold", "move_stop")
    assert "ride latch" in a.note


def test_ride_exits_on_trendflex_flip():
    em = ExitManager()
    a = em.evaluate(P(ride_mode=True), px=120.0, bar_high=121.0, bar_low=119.0,
                    tf_row=row(trendflex=-0.1))
    assert a.kind == "exit" and a.exit_reason == "trendflex"


def test_ride_holds_while_trendflex_positive():
    em = ExitManager()
    a = em.evaluate(P(ride_mode=True), px=150.0, bar_high=150.0, bar_low=149.0,
                    tf_row=row(trendflex=1.2))
    assert a.kind in ("hold", "move_stop")     # не выходим, TP игнорируется


def test_r_progression_moves_stop_to_breakeven():
    em = ExitManager()
    p = P(ride_mode=True)                       # risk=2 → +1R = 102
    a = em.evaluate(p, px=102.5, bar_high=102.5, bar_low=101.0,
                    tf_row=row(trendflex=0.6))
    assert a.kind == "move_stop"
    assert a.new_stop >= p.entry                # BE или выше


def test_r_progression_locks_plus_1r():
    em = ExitManager()
    p = P(ride_mode=True)                       # +2R = 104 → стоп 102
    a = em.evaluate(p, px=104.5, bar_high=104.5, bar_low=103.0,
                    tf_row=row(trendflex=0.6))
    assert a.kind == "move_stop"
    assert a.new_stop >= p.entry + (p.entry - p.stop)


def test_stop_never_moves_down():
    em = ExitManager()
    p = P(ride_mode=True, stop=103.0)           # стоп уже подтянут выше BE
    a = em.evaluate(p, px=103.5, bar_high=103.5, bar_low=103.2,
                    tf_row=row(trendflex=0.6))
    # кандидаты ниже текущего стопа → hold, не опускаем
    assert a.kind == "hold" or (a.kind == "move_stop" and a.new_stop >= 103.0)


def test_emergency_flatten():
    em = ExitManager()
    a = em.evaluate(P(), px=100.0, bar_high=100.5, bar_low=99.5,
                    tf_row=row(), emergency=True)
    assert a.kind == "exit" and a.exit_reason == "emergency"
