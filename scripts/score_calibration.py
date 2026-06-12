# =========================
# scripts/score_calibration.py — TradingPalantir
# Калибровка Scoring v2: живой снапшот скоринга + распределение +
# сколько монет было бы armed при разных порогах + история из журнала.
# Запуск из tradingpalantir/:  python3 -m scripts.score_calibration
# =========================
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
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


async def main():
    reg = TokenRegistry(C.TOKEN_REGISTRY_FILE).load()
    try:
        mcp = CMCMcp()
    except CMCMCPError:
        mcp = None
    regime_engine = MarketRegimeEngine(mcp)
    deriv = DerivativesPressureEngine(mcp)
    radar = OpportunityRadar(reg, CMC(), mcp, derivatives=deriv,
                             feed=GeckoTerminalFeed("bsc"))

    regime = await regime_engine.read()
    perp = await deriv.read()
    thr = regime_engine.adaptive_threshold(regime, perp)
    print(f"режим: {regime['global_regime']}/{regime['market_state']} | "
          f"перп: {perp['pressure']} | адаптивный порог: {thr}")
    print("скан (Pass1: 128 → Pass2: топ-30 c GT-барами, ~2-3 мин)...\n")

    scored = await radar.scan()
    top = scored[:20]
    print(f"{'sym':8} {'score':>6} {'abs':>5} {'pct':>5}  liq  mom  brk  trd  soc")
    for t in top:
        c = t.components
        print(f"{t.symbol:8} {t.score:6.1f} {t.absolute:5.1f} {t.percentile:5.1f}  "
              f"{c.get('liquidity',0):4.1f} {c.get('momentum',0):4.1f} "
              f"{c.get('breakout',0):4.1f} {c.get('trend',0):4.1f} "
              f"{c.get('social',0):4.1f}")

    print("\nсколько монет armed при пороге:")
    for th in (80, 85, 88, 90, 92):
        n = sum(1 for t in scored if t.score >= th)
        print(f"  >= {th}: {n}")

    # история score из журнала (если есть)
    if os.path.exists(C.JOURNAL_DB):
        con = sqlite3.connect(C.JOURNAL_DB)
        rows = con.execute(
            "SELECT payload FROM events WHERE event='SCORE_UPDATED'").fetchall()
        if rows:
            vals = [json.loads(r[0]).get("score", 0) for r in rows]
            vals.sort()
            n = len(vals)
            print(f"\nистория журнала: {n} записей score | "
                  f"p50={vals[n//2]:.0f} p90={vals[int(n*0.9)]:.0f} "
                  f"p99={vals[int(n*0.99)]:.0f} max={vals[-1]:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
