"""Замок на агрессивный sizing в config/rules.json (2 позиции по ~50% книги).

Live-стратегия (то, что реально торгует и видят судьи) задаётся rules.json —
эти тесты фиксируют намерение и ловят случайный откат значений.
"""
import pytest

import config as C
from risk.core import RuleBook
from risk.risk_governor import RiskGovernor


def test_rules_json_aggressive_intent():
    r = RuleBook.load(C.RULES_FILE)
    assert r.max_concurrent_positions == 2
    assert r.max_position_notional_usdt == 22.5      # ~46% от $50, 2× = $45 + $5 резерв
    assert r.max_risk_per_trade_usdt == 12.0         # высоко → notional-cap связывает
    assert r.one_position_per_symbol is True          # 2 позиции = 2 разные монеты
    assert r.long_only is True


def test_position_sizes_to_half_book(journal):
    r = RuleBook.load(C.RULES_FILE)
    gov = RiskGovernor(r, journal)
    # стоп 10% → risk-notional 12/0.10 = $120 >> cap → режется до ~$22.5
    appr = gov.approve_entry(symbol="X", address=r.allowed_tokens[0],
                             entry=100.0, stop=90.0, open_positions=0)
    assert appr["approved"]
    assert appr["notional"] == pytest.approx(22.5, rel=1e-3)


def test_two_positions_ok_third_rejected(journal):
    r = RuleBook.load(C.RULES_FILE)
    gov = RiskGovernor(r, journal)
    addr = r.allowed_tokens[0]
    assert gov.approve_entry(symbol="X", address=addr, entry=100.0, stop=90.0,
                             open_positions=1)["approved"]          # 2-я позиция — ок
    assert not gov.approve_entry(symbol="X", address=addr, entry=100.0, stop=90.0,
                                 open_positions=2)["approved"]      # 3-я — отказ
