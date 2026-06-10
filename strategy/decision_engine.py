# =========================
# strategy/decision_engine.py — TradingPalantir
# Decision Engine (§17): склейка сигнал → score-гейт → LLM two-pass →
# Risk Governor → действие. LLM не исполняет; Governor — финальная инстанция.
# =========================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import config as C
from llm.gateway import LLMGateway
from risk.risk_governor import RiskGovernor
from journal import event_types as ET
from journal.trade_journal import TradeJournal


@dataclass
class TradeDecision:
    action: str                  # OPEN_POSITION | NO_TRADE
    symbol: str
    reason: str = ""
    qty: float = 0.0
    risk_usdt: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    tp: float = 0.0
    tf: str = ""
    setup: str = ""
    rationale: str = ""
    payload: Dict = field(default_factory=dict)


class DecisionEngine:
    def __init__(self, gateway: LLMGateway, governor: RiskGovernor,
                 journal: TradeJournal):
        self.gateway = gateway
        self.governor = governor
        self.journal = journal

    def evaluate_entry(self, *, signal: Dict, scored, regime: Dict,
                       cmc_ctx: Dict, open_positions: int,
                       evidence_payload: Optional[Dict] = None) -> TradeDecision:
        sym = signal["symbol"]
        self.journal.log(ET.TRADE_SIGNAL_CREATED, symbol=sym,
                         tf=signal["tf"], setup=signal["type"],
                         entry=signal["entry"], stop=signal["stop"])

        # 1) score-гейт (Stage B): вход только если монета armed
        if scored is not None and not scored.armed:
            self.journal.log(ET.TRADE_REJECTED, symbol=sym,
                             reason=f"score {scored.score} < {C.SCORE_ENTRY_THRESHOLD}")
            return TradeDecision("NO_TRADE", sym,
                                 reason=f"score {scored.score} ниже порога")

        # 2) LLM two-pass (analyst + reviewer)
        g = self.gateway.decide(
            signal={"type": signal["type"], "direction": "bull"},
            indicators={"entry": signal["entry"], "stop": signal["stop"],
                        "tp": signal["tp"], "timeframe": signal["tf"],
                        "ema": signal["row"].get("ema"),
                        "vwap": signal["row"].get("vwap"),
                        "trendflex": signal["row"].get("trendflex"),
                        "money_flow": signal["row"].get("money_flow")},
            risk_ctx={"open_positions": open_positions,
                      "guard_mode": self.governor.guard.mode,
                      "score": scored.score if scored else None},
            cmc_ctx=cmc_ctx,
            rules_summary=(f"long-only spot; max {self.governor.rules.max_concurrent_positions} pos; "
                           f"DD tiers {C.DD_DEFENSIVE_PCT}/{C.DD_BLOCK_PCT}/{C.DD_FLATTEN_PCT}%; "
                           f"regime={regime.get('global_regime')} state={regime.get('market_state')}"),
            evidence_payload=evidence_payload)
        self.journal.log(ET.LLM_ANALYSIS_CREATED, symbol=sym,
                         enter=g.enter, size_factor=g.size_factor,
                         rationale=g.rationale, source=g.source)
        if not g.enter:
            self.journal.log(ET.TRADE_REJECTED, symbol=sym, reason="LLM veto",
                             rationale=g.rationale)
            return TradeDecision("NO_TRADE", sym, reason="LLM veto",
                                 rationale=g.rationale)

        # 3) Risk Governor — финальная инстанция
        appr = self.governor.approve_entry(
            symbol=sym, address=signal["address"], entry=signal["entry"],
            stop=signal["stop"], open_positions=open_positions,
            size_factor=g.size_factor)
        if not appr["approved"]:
            return TradeDecision("NO_TRADE", sym,
                                 reason=appr["rejection_reason"],
                                 rationale=g.rationale)
        return TradeDecision(
            "OPEN_POSITION", sym, qty=appr["qty"], risk_usdt=appr["risk_usdt"],
            entry=signal["entry"], stop=signal["stop"], tp=signal["tp"],
            tf=signal["tf"], setup=signal["type"], rationale=g.rationale,
            payload={"notional": appr["notional"], "risk_mode": appr["risk_mode"],
                     "size_factor": g.size_factor})
