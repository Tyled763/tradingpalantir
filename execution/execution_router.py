# =========================
# execution/execution_router.py — TradingPalantir
# Маршрутизатор paper|live (§20). Live = TWAK adapter (порт twak_exec):
# вход swap USDT→token + SL-автоматизация; выход swap token→USDT (+снятие SL).
# Режим из config.DRY_RUN: True → paper. Исполняется ТОЛЬКО после Risk Governor.
# =========================
from __future__ import annotations

from typing import Dict, Optional

import config as C
from execution.paper_broker import PaperBroker
from execution.twak_adapter import TwakExec, TwakError


class ExecutionRouter:
    def __init__(self, twak: Optional[TwakExec] = None,
                 paper: Optional[PaperBroker] = None):
        self.paper = paper or PaperBroker()
        self.twak = twak
        self.live = not C.DRY_RUN

    async def round_trip(self, *, address: str, usdt: float) -> Dict:
        """
        Daily-compliance микро-сделка: купить токен на usdt → сразу продать.
        Гарантирует on-chain trade без позиции/слота. Стоимость ≈ газ + fee+slip.
        paper: симуляция; live: twak swap USDT→token, затем token→USDT.
        """
        if not self.live:
            fee = usdt * C.ROUNDTRIP_FEE + usdt * C.EXPECTED_SLIPPAGE * 2
            self.paper.realized_pnl -= fee
            self.paper.fills += 2
            return {"status": "filled", "kind": "round_trip", "cost": round(fee, 4),
                    "tx": f"paper-rt-{self.paper.fills}"}
        buy = await self.twak.swap(round(usdt, 6), C.QUOTE_CCY, address)
        got = float(str(buy.get("output", "0")).split()[0] or 0)
        sell = await self.twak.swap(got, address, C.QUOTE_CCY)
        return {"status": "filled", "kind": "round_trip",
                "tx": buy.get("txHash") or buy.get("hash"),
                "tx_sell": sell.get("txHash") or sell.get("hash")}

    async def equity(self) -> float:
        if self.live and self.twak is not None:
            try:
                p = await self.twak.portfolio()
                v = float(p.get("totalUsd") or p.get("total") or 0.0)
                if v > 0:
                    return v
            except TwakError:
                pass
        return self.paper.equity

    async def open_long(self, *, symbol: str, address: str, qty: float,
                        px: float, stop: float) -> Dict:
        """Возвращает {status, px, tx, sl_automation_id?}."""
        if not self.live:
            r = await self.paper.buy(symbol=symbol, address=address, qty=qty, px=px)
            r["sl_automation_id"] = None
            return r
        sw = await self.twak.swap(round(px * qty, 6), C.QUOTE_CCY, address)
        sl = None
        try:
            sl_r = await self.twak.place_sl(address, qty, stop)
            sl = str(sl_r.get("id") or sl_r.get("automationId") or "") or None
        except TwakError:
            pass   # позиция открыта, стоп ведёт агент программно (logged выше)
        return {"status": "filled", "px": px,
                "tx": sw.get("txHash") or sw.get("hash"),
                "sl_automation_id": sl}

    async def close_long(self, *, symbol: str, address: str, qty: float,
                         px: float, entry_px: float,
                         sl_automation_id: Optional[str] = None) -> Dict:
        if not self.live:
            return await self.paper.sell(symbol=symbol, address=address,
                                         qty=qty, px=px, entry_px=entry_px)
        sw = await self.twak.swap(qty, address, C.QUOTE_CCY)
        if sl_automation_id:
            try:
                await self.twak.delete_automation(str(sl_automation_id))
            except TwakError:
                pass
        fee = (entry_px + px) * qty * C.ROUNDTRIP_FEE
        return {"status": "filled", "px": px,
                "pnl": round((px - entry_px) * qty - fee, 6),
                "tx": sw.get("txHash") or sw.get("hash")}

    async def move_stop(self, *, address: str, qty: float, new_stop: float,
                        sl_automation_id: Optional[str]) -> Optional[str]:
        """Пересоздаёт SL-автоматизацию (twak amend нет) → новый id."""
        if not self.live:
            return sl_automation_id
        try:
            if sl_automation_id:
                await self.twak.delete_automation(str(sl_automation_id))
            r = await self.twak.place_sl(address, qty, new_stop)
            return str(r.get("id") or r.get("automationId") or "") or None
        except TwakError:
            return sl_automation_id
