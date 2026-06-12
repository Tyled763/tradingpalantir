import asyncio
from types import SimpleNamespace

import pytest

import config as C
from intelligence.opportunity_radar import OpportunityRadar, ScoredToken


class FakeRegistry:
    def __init__(self, n=10):
        self._e = [SimpleNamespace(symbol=f"T{i}", address=f"0x{i:040x}",
                                   pool=f"p{i}", cmc_id=i) for i in range(n)]

    def with_pools(self):
        return self._e


class FakeCMC:
    def __init__(self, quotes):
        self._q = quotes

    async def quotes(self, symbols):
        return self._q


def mk_radar(quotes, feed=None, firewall=None):
    r = OpportunityRadar.__new__(OpportunityRadar)
    r.registry = FakeRegistry(len(quotes))
    r.cmc = FakeCMC(quotes)
    r.mcp = None
    r.derivatives = None
    r.firewall = firewall
    r.feed = feed
    r._news_cache = {}
    return r


def quotes_for(n, vol=5e7, chg=2.0):
    return {f"T{i}": {"volume_24h": vol, "change_24h": chg, "change_7d": chg * 3}
            for i in range(n)}


def test_pass1_liquidity_scale():
    p = OpportunityRadar._pass1
    assert p(1e6, 0, 0)["liquidity"] < 5
    assert p(1e8, 0, 0)["liquidity"] == pytest.approx(17.5, abs=1)
    assert p(1e9, 0, 0)["liquidity"] == 24.5  # ~cap
    assert p(1e11, 0, 0)["liquidity"] == 25.0


def test_pass1_momentum_overheat_penalized():
    p = OpportunityRadar._pass1
    healthy = p(1e8, 8.0, 20.0)["momentum"]
    pumped = p(1e8, 60.0, 100.0)["momentum"]
    assert healthy > pumped


def test_hybrid_score_spread_and_top_reachable():
    # одна явно лучшая монета: высокая ликвидность+momentum
    q = quotes_for(10, vol=2e7, chg=0.5)
    q["T0"] = {"volume_24h": 8e8, "change_24h": 9.0, "change_7d": 25.0}
    r = mk_radar(q)
    scored = asyncio.run(r.scan())
    best = scored[0]
    assert best.symbol == "T0"
    # перцентиль даёт топу ~30 баллов сверху абсолюта
    assert best.score > best.absolute * 0.7 + 25
    # спред: лучшая заметно выше медианы
    assert best.score - scored[len(scored) // 2].score > 10


def test_armed_threshold_applied():
    q = quotes_for(5)
    r = mk_radar(q)
    res = asyncio.run(r.funnel(threshold=99.0))
    assert res["armed"] == []          # никто не дотянул
    res = asyncio.run(r.funnel(threshold=1.0))
    assert len(res["armed"]) > 0       # все прошли


def test_firewall_caps_below_threshold():
    class FW:
        async def check(self, *a, **k):
            return {"status": "rejected", "reasons": ["test"]}
    q = quotes_for(3, vol=9e8, chg=10.0)
    r = mk_radar(q, firewall=FW())
    res = asyncio.run(r.funnel(threshold=85.0))
    assert all(t.score <= 40.0 for t in res["watchlist"])
    assert res["armed"] == []
