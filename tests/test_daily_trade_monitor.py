import time
from types import SimpleNamespace

import config as C
from journal import event_types as ET
from risk.daily_trade_monitor import DailyTradeMonitor, in_live_window


def test_counts_only_today(journal):
    m = DailyTradeMonitor(journal)
    assert m.trades_today() == 0
    journal.log(ET.ORDER_FILLED, symbol="ETH", kind="entry")
    assert m.trades_today() == 1


def test_no_fallback_outside_live_window(journal, monkeypatch):
    monkeypatch.setattr("risk.daily_trade_monitor.in_live_window", lambda ts=None: False)
    m = DailyTradeMonitor(journal)
    res = m.check("normal")
    assert not res["needs_fallback"]


def test_fallback_when_window_closing(journal, monkeypatch):
    monkeypatch.setattr("risk.daily_trade_monitor.in_live_window", lambda ts=None: True)
    m = DailyTradeMonitor(journal)
    monkeypatch.setattr(m, "seconds_left_today", lambda: 1.5 * 3600)  # < 3ч
    res = m.check("normal")
    assert res["needs_fallback"]


def test_fallback_blocked_by_guard(journal, monkeypatch):
    monkeypatch.setattr("risk.daily_trade_monitor.in_live_window", lambda ts=None: True)
    m = DailyTradeMonitor(journal)
    monkeypatch.setattr(m, "seconds_left_today", lambda: 1.0 * 3600)
    res = m.check("block_new_trades")
    assert not res["needs_fallback"]
    assert "guard" in res["reason"]


def test_satisfied_after_trade(journal, monkeypatch):
    monkeypatch.setattr("risk.daily_trade_monitor.in_live_window", lambda ts=None: True)
    journal.log(ET.ORDER_FILLED, symbol="ETH", kind="entry")
    m = DailyTradeMonitor(journal)
    res = m.check("normal")
    assert res["satisfied"] and not res["needs_fallback"]


def test_fallback_candidate_prefers_tier1(journal):
    m = DailyTradeMonitor(journal)
    wl = [SimpleNamespace(symbol="NEX", address="0x1", pool="p1", firewall="approved"),
          SimpleNamespace(symbol="ETH", address="0x2", pool="p2", firewall="approved")]
    c = m.fallback_candidate(wl)
    assert c["symbol"] == "ETH" and c["kind"] == "fallback"


def test_fallback_candidate_skips_rejected(journal):
    m = DailyTradeMonitor(journal)
    wl = [SimpleNamespace(symbol="ETH", address="0x2", pool="p2", firewall="rejected")]
    assert m.fallback_candidate(wl) is None
