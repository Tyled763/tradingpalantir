# =========================
# scripts/diag_signals.py — TradingPalantir (read-only диагностика)
# Проверяет, что Stage C ДЕТЕКТИТ сетапы по live-пути (EntrySignalEngine._detect),
# а не только синхронный replay: warmup armed-набора → по каждому символу прогон
# последних N баров 5m через ту же логику детекции, что и онлайн-poll.
# Запуск:  python3 -m scripts.diag_signals
# =========================
from __future__ import annotations

import asyncio
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import config as C
from cmc.cmc_client import CMC
from cmc.mcp_client import CMCMcp, CMCMCPError
from intelligence.derivatives_pressure_engine import DerivativesPressureEngine
from intelligence.market_regime_engine import MarketRegimeEngine
from intelligence.opportunity_radar import OpportunityRadar
from marketdata.gt_feed import GeckoTerminalFeed
from marketdata.token_registry import TokenRegistry
from strategy.entry_signal_engine import EntrySignalEngine


async def main():
    reg = TokenRegistry(C.TOKEN_REGISTRY_FILE).load()
    try:
        mcp = CMCMcp()
    except CMCMCPError:
        mcp = None
    feed = GeckoTerminalFeed("bsc")
    radar = OpportunityRadar(reg, CMC(), mcp,
                             derivatives=DerivativesPressureEngine(mcp), feed=feed)
    regime_engine = MarketRegimeEngine(mcp)
    regime = await regime_engine.read()
    thr = regime_engine.adaptive_threshold(regime, await radar.derivatives.read())
    funnel = await radar.funnel(thr)
    armed = funnel["armed"]
    print(f"режим {regime['global_regime']} | порог {thr} | armed ({len(armed)}): "
          f"{[t.symbol for t in armed]}\n")

    ese = EntrySignalEngine(feed)
    total = 0
    for t in armed:
        await ese.watch(t.symbol, t.address, t.pool)
        # «проигрываем» последние 80 баров 5m через ту же _detect, что и онлайн
        bp = ese.proc.get((t.symbol, C.BASE_TF))
        if not bp or not bp.rows:
            print(f"  {t.symbol:8} нет данных 5m"); continue
        hits = 0
        for row in list(bp.rows)[-80:]:
            for sig in ese._detect(t.symbol, C.BASE_TF, row):
                hits += 1
        # плюс проверим старшие ТФ
        for tf in ("15m", "30m", "1H"):
            p = ese.proc.get((t.symbol, tf))
            if p and p.rows:
                for row in list(p.rows)[-60:]:
                    hits += len(ese._detect(t.symbol, tf, row))
        total += hits
        print(f"  {t.symbol:8} setups в недавней истории: {hits}")
    print(f"\nИТОГО сетапов по armed-набору (недавняя история): {total}")
    print("Вывод: если >0 — Stage C детекция работает; онлайн поймает их на закрытии баров.")


if __name__ == "__main__":
    asyncio.run(main())
