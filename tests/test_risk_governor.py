import pytest

from risk.risk_governor import RiskGovernor


@pytest.fixture
def gov(rules, journal):
    return RiskGovernor(rules, journal)


def test_allowlist_rejects_unknown_token(gov):
    r = gov.approve_entry(symbol="EVIL", address="0xdead", entry=100, stop=98,
                          open_positions=0)
    assert not r["approved"]
    assert "allowlist" in r["rejection_reason"]


def test_allowlist_case_insensitive(gov):
    r = gov.approve_entry(symbol="OK", address="0xAAA", entry=100, stop=98,
                          open_positions=0)
    assert r["approved"]


def test_position_limit(gov):
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=98,
                          open_positions=2)
    assert not r["approved"]
    assert "лимит позиций" in r["rejection_reason"]


def test_stop_must_be_below_entry(gov):
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=101,
                          open_positions=0)
    assert not r["approved"]


def test_notional_capped(gov):
    # тугой стоп 0.2% → риск-сайзинг хочет огромный ноционал → срез до $45
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=99.8,
                          open_positions=0)
    assert r["approved"]
    assert r["notional"] <= 45.0 + 1e-6


def test_size_factor_only_reduces(gov):
    base = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                             open_positions=0, size_factor=1.0)
    half = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                             open_positions=0, size_factor=0.5)
    boosted = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                                open_positions=0, size_factor=5.0)  # clamp -> 1.0
    assert half["risk_usdt"] == pytest.approx(base["risk_usdt"] * 0.5)
    assert boosted["risk_usdt"] == pytest.approx(base["risk_usdt"])


def test_drawdown_block_stops_entries(gov):
    gov.update_drawdown(19.0)   # >= DD_BLOCK_PCT (18)
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=98,
                          open_positions=0)
    assert not r["approved"]


def test_defensive_halves_risk(gov):
    base = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                             open_positions=0)
    gov.update_drawdown(13.0)   # defensive (>= 12, < 18)
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                          open_positions=0)
    assert r["approved"]
    assert r["risk_usdt"] == pytest.approx(base["risk_usdt"] * 0.5)


def test_one_position_per_symbol_blocks_held(gov):
    # монета уже в позиции → reject, даже если всё остальное ок
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                          open_positions=0, held_symbols={"OK"})
    assert not r["approved"]
    assert "one-per-symbol" in r["rejection_reason"]


def test_one_position_per_symbol_allows_free(gov):
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=95,
                          open_positions=0, held_symbols={"OTHER"})
    assert r["approved"]


def test_defensive_halves_notional_cap(gov):
    # тугой стоп → ноционал упирается в cap; defensive должен ужать cap вдвое
    base = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=99.8,
                             open_positions=0)
    gov.update_drawdown(13.0)   # defensive
    r = gov.approve_entry(symbol="OK", address="0xaaa", entry=100, stop=99.8,
                          open_positions=0)
    assert r["approved"]
    assert r["notional"] == pytest.approx(base["notional"] * 0.5, rel=1e-3)
