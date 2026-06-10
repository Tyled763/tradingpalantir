# =========================
# execution/paper_broker.py — TradingPalantir
# Бумажный брокер (§20: «Never implement live execution before paper mode»).
# Мимикрирует: вход/выход, комиссии+слиппедж, PnL, equity. Котировки может
# (опц.) сверять через twak quote-only — но не обязан (replay/оффлайн).
# =========================
from __future__ import annotations

import time
from typing import Dict, Optional

import config as C


class PaperBroker:
    def __init__(self, starting_equity: float = None):
        self.starting_equity = starting_equity or C.PAPER_EQUITY
        self.realized_pnl = 0.0
        self.fills: int = 0

    @property
    def equity(self) -> float:
        return self.starting_equity + self.realized_pnl

    async def buy(self, *, symbol: str, address: str, qty: float,
                  px: float) -> Dict:
        slip = px * C.EXPECTED_SLIPPAGE
        fill_px = px + slip
        self.fills += 1
        return {"status": "filled", "side": "buy", "symbol": symbol,
                "qty": qty, "px": fill_px, "fee": fill_px * qty * C.ROUNDTRIP_FEE / 2,
                "tx": f"paper-{int(time.time()*1000)}", "ts": time.time()}

    async def sell(self, *, symbol: str, address: str, qty: float,
                   px: float, entry_px: float) -> Dict:
        slip = px * C.EXPECTED_SLIPPAGE
        fill_px = px - slip
        fee = (entry_px + fill_px) * qty * C.ROUNDTRIP_FEE
        pnl = (fill_px - entry_px) * qty - fee
        self.realized_pnl += pnl
        self.fills += 1
        return {"status": "filled", "side": "sell", "symbol": symbol,
                "qty": qty, "px": fill_px, "pnl": round(pnl, 6),
                "tx": f"paper-{int(time.time()*1000)}", "ts": time.time()}
