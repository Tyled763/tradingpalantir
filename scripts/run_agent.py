# =========================
# scripts/run_agent.py — TradingPalantir
# Главный цикл. Operating-флоу (требование пользователя):
#
#   Stage A (rescreen, ~25 мин): скоринг всех 128 → watchlist топ-20
#   Stage B: armed = score >= 90 (+ firewall approved)
#   Stage C: бар-мониторинг ТОЛЬКО armed (+ монеты с открытыми позициями)
#            → точка входа стратегии пользователя (FVG/VWAP/EMA)
#   Вход:  score-гейт → Claude analyst → Claude reviewer → Risk Governor →
#          ExecutionRouter (paper|live) → журнал
#   Ведение: ExitManager (normal TP/SL → confluence латчит ride →
#            trendflex-флип; страховочная подтяжка стопа R/ATR; SL всегда)
#   Сервисы: DrawdownGuard (8/12/18%), DailyTradeMonitor (1 сделка/день).
#
# Запуск из tradingpalantir/:  python3 -m scripts.run_agent
# =========================
from __future__ import annotations

import asyncio
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import config as C
from cmc.cmc_client import CMC
from cmc.mcp_client import CMCMcp, CMCMCPError
from execution.execution_router import ExecutionRouter
from execution.twak_adapter import TwakExec
from exit.exit_manager import ExitManager
from intelligence.derivatives_pressure_engine import DerivativesPressureEngine
from intelligence.market_regime_engine import MarketRegimeEngine
from intelligence.opportunity_radar import OpportunityRadar
from intelligence.token_risk_firewall import TokenRiskFirewall
from journal import event_types as ET
from journal.trade_journal import TradeJournal
from llm.gateway import LLMGateway
from marketdata.gt_feed import GeckoTerminalFeed
from marketdata.token_registry import TokenRegistry
from risk.core import RuleBook, PositionRegistry, PosState
from risk.daily_trade_monitor import DailyTradeMonitor, in_live_window
from risk.risk_governor import RiskGovernor
from strategy.decision_engine import DecisionEngine, TradeDecision
from strategy.entry_signal_engine import EntrySignalEngine


class TradingPalantir:
    def __init__(self):
        self.journal = TradeJournal()
        self.registry = TokenRegistry(C.TOKEN_REGISTRY_FILE).load()
        self.rules = RuleBook.load(C.RULES_FILE)
        self.positions = PositionRegistry(C.POSITIONS_FILE)
        self.feed = GeckoTerminalFeed("bsc")
        self.twak = TwakExec(chain="bsc")
        self.router = ExecutionRouter(twak=self.twak)
        self.cmc = CMC()
        try:
            self.mcp = CMCMcp()
        except CMCMCPError:
            self.mcp = None
        self.regime_engine = MarketRegimeEngine(self.mcp)
        self.derivatives = DerivativesPressureEngine(self.mcp)
        self.firewall = TokenRiskFirewall(twak=self.twak, feed=self.feed)
        self.radar = OpportunityRadar(self.registry, self.cmc, self.mcp,
                                      derivatives=self.derivatives,
                                      firewall=self.firewall, feed=self.feed)
        self.ese = EntrySignalEngine(self.feed)
        self.governor = RiskGovernor(self.rules, self.journal)
        self.gateway = LLMGateway()
        self.decisions = DecisionEngine(self.gateway, self.governor, self.journal)
        self.exits = ExitManager()
        self.daily = DailyTradeMonitor(self.journal)

        self.regime: dict = {"global_regime": "neutral", "risk_budget": "normal"}
        self.watchlist: list = []
        self.scored_by_symbol: dict = {}
        self._last_rescreen = 0.0
        self._day_anchor_equity: float = 0.0
        self._day_anchor_ts: float = 0.0

    # ── Stage A+B: режим + скоринг + armed set ────────────
    async def rescreen(self) -> None:
        self.regime = await self.regime_engine.read()
        self.journal.log(ET.REGIME_UPDATED, **{k: self.regime[k] for k in
                         ("global_regime", "market_state", "risk_budget")})
        perp = await self.derivatives.read()
        threshold = self.regime_engine.adaptive_threshold(self.regime, perp)
        funnel = await self.radar.funnel(threshold)
        self.watchlist = funnel["watchlist"]
        self.scored_by_symbol = {t.symbol: t for t in funnel["scored"]}
        armed = funnel["armed"]
        self.journal.log(ET.WATCHLIST_UPDATED,
                         watchlist=[(t.symbol, t.score) for t in self.watchlist])
        self.journal.log(ET.ARMED_SET_UPDATED,
                         armed=[(t.symbol, t.score) for t in armed],
                         threshold=threshold)
        for t in self.watchlist:
            self.journal.log(ET.SCORE_UPDATED, symbol=t.symbol, score=t.score,
                             components=t.components, firewall=t.firewall)

        # Stage C: мониторим armed + монеты с открытыми позициями
        held = {p.symbol for p in self.positions.open_positions()}
        want = {t.symbol for t in armed} | held
        for t in armed:
            if t.symbol not in self.ese.monitored():
                await self.ese.watch(t.symbol, t.address, t.pool)
        for sym in list(self.ese.monitored()):
            if sym not in want:
                self.ese.unwatch(sym)
        self._last_rescreen = time.time()
        print(f"[TP] regime={self.regime.get('global_regime')}/"
              f"{self.regime.get('market_state')} | "
              f"watchlist={[(t.symbol, t.score) for t in self.watchlist[:6]]}… | "
              f"thr={threshold} | armed={[t.symbol for t in armed]} | "
              f"monitoring={self.ese.monitored()}")

    # ── Stage C → вход ────────────────────────────────────
    async def _handle_signal(self, sig: dict) -> None:
        sym = sig["symbol"]
        scored = self.scored_by_symbol.get(sym)
        meta = self.ese._meta.get(sym) or {}
        sig["address"] = meta.get("address") or (scored.address if scored else None)
        if not sig["address"]:
            return
        cmc_ctx = await self._cmc_context(sym, scored)
        decision = self.decisions.evaluate_entry(
            signal=sig, scored=scored, regime=self.regime, cmc_ctx=cmc_ctx,
            open_positions=len(self.positions.open_positions()))
        if decision.action != "OPEN_POSITION":
            return
        await self._open(decision, sig)

    async def _cmc_context(self, sym: str, scored) -> dict:
        ctx = {"macro_regime": self.regime.get("global_regime"),
               "market_state": self.regime.get("market_state"),
               "score": scored.score if scored else None,
               "score_components": scored.components if scored else None,
               "perp_pressure": (await self.derivatives.read()).get("pressure")}
        try:
            q = await self.cmc.quote("BNB" if sym == "WBNB" else sym)
            ctx.update({"price": q.get("price"), "change_24h": q.get("change_24h"),
                        "volume_24h": q.get("volume_24h")})
        except Exception:
            pass
        if self.mcp and scored:
            try:
                news = await self.mcp.latest_news(scored.cmc_id, limit=3)
                ctx["news"] = [n.get("title") for n in news if n.get("title")]
            except Exception:
                pass
        return ctx

    async def _open(self, d: TradeDecision, sig: dict) -> None:
        pos = self.positions.new(symbol=d.symbol, direction="bull", entry=d.entry,
                                 stop=d.stop, tp=d.tp, qty=d.qty,
                                 risk_usdt=d.risk_usdt, setup=d.setup, tf=d.tf,
                                 rationale=d.rationale)
        self.journal.log(ET.ORDER_SUBMITTED, symbol=d.symbol, sid=pos.sid,
                         qty=d.qty, entry=d.entry, stop=d.stop, tp=d.tp,
                         **d.payload)
        try:
            r = await self.router.open_long(symbol=d.symbol, address=sig["address"],
                                            qty=d.qty, px=d.entry, stop=d.stop)
        except Exception as e:
            self.positions.update(pos.sid, state=PosState.CANCELED.value)
            self.journal.log(ET.TRADE_REJECTED, symbol=d.symbol,
                             reason=f"execution error: {e}")
            return
        self.positions.update(pos.sid, state=PosState.OPEN.value,
                              avg_px=r.get("px", d.entry), tx_enter=r.get("tx"),
                              sl_automation_id=r.get("sl_automation_id"))
        self.journal.log(ET.ORDER_FILLED, symbol=d.symbol, sid=pos.sid,
                         px=r.get("px"), tx=r.get("tx"), kind="entry")
        print(f"[TP] OPEN #{pos.sid} {d.symbol} {d.setup} tf={d.tf} "
              f"entry={d.entry:.6g} stop={d.stop:.6g} qty={d.qty:.6g} "
              f"({'paper' if C.DRY_RUN else 'LIVE'})")

    # ── ведение позиций ───────────────────────────────────
    async def manage_positions(self) -> None:
        flatten = self.governor.guard.must_flatten
        for p in [x for x in self.positions.open_positions()
                  if x.state == PosState.OPEN.value]:
            base = self.ese.row(p.symbol, C.BASE_TF)
            if base is None:
                continue
            tf_row = self.ese.row(p.symbol, p.tf) or base
            px = float(base["close"])
            act = self.exits.evaluate(
                p, px=px, bar_high=float(base["high"]), bar_low=float(base["low"]),
                tf_row=tf_row, emergency=flatten)
            # латч ride (определяется в evaluate через confluence)
            if not p.ride_mode and act.note.startswith("ride latch"):
                self.positions.update(p.sid, ride_mode=True)
                p.ride_mode = True
                self.journal.log(ET.RIDE_MODE_ON, symbol=p.symbol, sid=p.sid)
            if act.kind == "move_stop":
                new_sl = await self.router.move_stop(
                    address=(self.ese._meta.get(p.symbol) or {}).get("address", ""),
                    qty=p.qty, new_stop=act.new_stop,
                    sl_automation_id=getattr(p, "sl_automation_id", None))
                self.positions.update(p.sid, stop=act.new_stop,
                                      sl_automation_id=new_sl)
                self.journal.log(ET.STOP_MOVED, symbol=p.symbol, sid=p.sid,
                                 new_stop=act.new_stop, note=act.note)
            elif act.kind == "exit":
                await self._close(p, act.exit_px, act.exit_reason)

    async def _close(self, p, px: float, reason: str) -> None:
        meta = self.ese._meta.get(p.symbol) or {}
        try:
            r = await self.router.close_long(
                symbol=p.symbol, address=meta.get("address", ""), qty=p.qty,
                px=px, entry_px=p.avg_px or p.entry,
                sl_automation_id=getattr(p, "sl_automation_id", None))
        except Exception as e:
            self.journal.log(ET.EXIT_WARNING, symbol=p.symbol, sid=p.sid,
                             note=f"close failed: {e}")
            return
        pnl = r.get("pnl", 0.0)
        self.positions.update(p.sid, state=PosState.CLOSED.value,
                              closed_at=time.time(), realized_pnl=pnl)
        self.exits.forget(p.sid)
        self.journal.log(ET.FULL_CLOSE, symbol=p.symbol, sid=p.sid, reason=reason,
                         px=px, pnl=pnl, ride=p.ride_mode)
        self.journal.log(ET.ORDER_FILLED, symbol=p.symbol, sid=p.sid, px=px,
                         kind="exit")
        print(f"[TP] CLOSE #{p.sid} {p.symbol} {reason}@{px:.6g} pnl={pnl:+.4f} "
              f"ride={p.ride_mode}")

    # ── сервисы: drawdown, daily monitor ──────────────────
    async def services(self) -> None:
        eq = await self.router.equity()
        now = time.time()
        if self._day_anchor_ts == 0 or now - self._day_anchor_ts > 86400:
            self._day_anchor_equity, self._day_anchor_ts = eq, now
        dd = max(0.0, (self._day_anchor_equity - eq) / self._day_anchor_equity * 100) \
            if self._day_anchor_equity else 0.0
        self.governor.update_drawdown(dd)
        if in_live_window():
            chk = self.daily.check(self.governor.guard.mode)
            if chk["needs_fallback"]:
                cand = self.daily.fallback_candidate(self.watchlist)
                if cand:
                    self.journal.log(ET.FALLBACK_TRADE, symbol=cand["symbol"],
                                     note=chk["reason"])
                    # fallback: минимальный размер через все гейты
                    row = self.ese.row(cand["symbol"], C.BASE_TF)
                    if row is not None:
                        px = float(row["close"])
                        sig = {"symbol": cand["symbol"], "tf": C.BASE_TF,
                               "type": "fallback", "direction": "bull",
                               "entry": px, "stop": px * 0.98, "tp": px * 1.02,
                               "row": row, "address": cand["address"]}
                        await self._handle_signal(sig)

    # ── главный цикл ──────────────────────────────────────
    async def run(self) -> None:
        if not self.registry.with_pools():
            print("[TP] реестр пуст — сначала зарегистрируйте токены"); return
        print(f"[TP] TradingPalantir старт · paper={C.DRY_RUN} · "
              f"brain={self.gateway.analyst.active} · "
              f"reviewer={self.gateway._reviewer_client is not None} · "
              f"threshold={C.SCORE_ENTRY_THRESHOLD}")
        await self.rescreen()
        while True:
            try:
                if time.time() - self._last_rescreen >= C.RESCREEN_INTERVAL_SEC:
                    await self.rescreen()
                for sig in await self.ese.poll():
                    await self._handle_signal(sig)
                await self.manage_positions()
                await self.services()
            except Exception as e:
                print(f"[TP] tick error: {type(e).__name__}: {e}")
            await asyncio.sleep(C.POLL_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(TradingPalantir().run())
