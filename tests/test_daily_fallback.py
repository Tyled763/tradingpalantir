import asyncio

import config as C
from execution.execution_router import ExecutionRouter
from execution.paper_broker import PaperBroker


def test_round_trip_paper_costs_only_fees():
    pb = PaperBroker(starting_equity=50.0)
    r = ExecutionRouter(twak=None, paper=pb)
    r.live = False
    res = asyncio.run(r.round_trip(address="0x", usdt=5.0))
    assert res["status"] == "filled" and res["kind"] == "round_trip"
    # стоимость только комиссии+слиппедж, маленькая
    assert 0 < res["cost"] < 0.1
    assert pb.equity < 50.0 and pb.equity > 49.9   # принципал сохранён, ушли копейки


def test_round_trip_two_fills():
    pb = PaperBroker(starting_equity=50.0)
    r = ExecutionRouter(twak=None, paper=pb)
    r.live = False
    asyncio.run(r.round_trip(address="0x", usdt=5.0))
    assert pb.fills == 2          # buy + sell


def test_force_live_window_env(monkeypatch):
    from risk.daily_trade_monitor import in_live_window
    monkeypatch.setenv("TP_FORCE_LIVE_WINDOW", "1")
    assert in_live_window() is True
    monkeypatch.delenv("TP_FORCE_LIVE_WINDOW")
    # вне окна (сегодня не 22-28.06) — False
    import datetime
    today = datetime.datetime.utcnow().date().isoformat()
    expected = C.LIVE_WINDOW[0] <= today <= C.LIVE_WINDOW[1]
    assert in_live_window() == expected
