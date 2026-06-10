import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config as C
from journal.trade_journal import TradeJournal
from risk.core import RuleBook


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(db_path=str(tmp_path / "j.db"),
                        jsonl_path=str(tmp_path / "j.jsonl"))


@pytest.fixture
def rules():
    return RuleBook(
        allowed_tokens=["0xaaa", "0xbbb"],
        max_risk_per_trade_usdt=2.5,
        max_concurrent_positions=2,
        max_daily_drawdown_pct=8.0,
        long_only=True,
        min_trade_notional_usdt=5.0,
        max_position_notional_usdt=45.0,
    )
