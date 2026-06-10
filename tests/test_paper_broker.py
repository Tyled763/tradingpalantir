import asyncio

import pytest

import config as C
from execution.paper_broker import PaperBroker


def test_roundtrip_pnl_includes_fees_and_slippage():
    b = PaperBroker(starting_equity=1000.0)

    async def run():
        buy = await b.buy(symbol="X", address="0x", qty=2.0, px=100.0)
        sell = await b.sell(symbol="X", address="0x", qty=2.0, px=110.0,
                            entry_px=buy["px"])
        return buy, sell

    buy, sell = asyncio.run(run())
    assert buy["px"] > 100.0                    # слиппедж против нас на входе
    assert sell["px"] < 110.0                   # и на выходе
    gross = (110.0 - 100.0) * 2.0
    assert 0 < sell["pnl"] < gross              # минус комиссии/слиппедж
    assert b.equity == pytest.approx(1000.0 + sell["pnl"])


def test_losing_trade_reduces_equity():
    b = PaperBroker(starting_equity=500.0)

    async def run():
        buy = await b.buy(symbol="X", address="0x", qty=1.0, px=100.0)
        return await b.sell(symbol="X", address="0x", qty=1.0, px=95.0,
                            entry_px=buy["px"])

    sell = asyncio.run(run())
    assert sell["pnl"] < 0
    assert b.equity < 500.0
