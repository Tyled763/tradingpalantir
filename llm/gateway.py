# =========================
# llm/gateway.py — TradingPalantir
# Claude two-pass (§16 спека, решение пользователя — без OpenAI):
#   1) ANALYST  (llm/claude_analyst.AgentBrain, порт) — confirm/veto/size_factor.
#   2) REVIEWER (этот файл) — независимый риск-ревью предложенного входа:
#      approve | downgrade (режет size_factor) | veto. Может только ужесточать.
# LLM никогда не исполняет сделки и не может поднять риск (capability-gating:
# финальный size_factor = min(analyst, reviewer) ≤ 1; Risk Governor — финальный).
# Нет ключа/ошибка → детерминированный fallback (торговлю не блокирует).
# =========================
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

import config as C
from llm.claude_analyst import AgentBrain, Decision

_REVIEW_SYSTEM = (
    "You are an independent RISK REVIEWER for an autonomous spot-trading agent on "
    "BNB Chain. An analyst has already approved a LONG entry. Your only job is to "
    "challenge it: look for hidden risks, inconsistency with the rules, stale or "
    "missing evidence, exhaustion, crowded positioning, macro stress. You may ONLY "
    "approve, downgrade (cut size_factor), or veto. You cannot increase size. "
    "You do not execute trades, sign transactions, or bypass risk controls. "
    "Answer ONLY by calling the review_decision tool. If evidence is missing or "
    "stale, say so explicitly and lean conservative."
)

_REVIEW_TOOL = {
    "name": "review_decision",
    "description": "Final risk review of a proposed trade entry.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["approve", "downgrade", "veto"]},
            "size_factor": {"type": "number",
                            "description": "0..1; applied only if downgrade"},
            "notes": {"type": "string"},
        },
        "required": ["verdict", "notes"],
    },
}


@dataclass
class GatewayDecision:
    enter: bool
    size_factor: float
    analyst_rationale: str
    reviewer_notes: str
    source: str            # llm+llm | llm+fallback | fallback

    @property
    def rationale(self) -> str:
        return f"ANALYST: {self.analyst_rationale} | REVIEWER: {self.reviewer_notes}"


class LLMGateway:
    def __init__(self, model: Optional[str] = None, mode: Optional[str] = None):
        self.analyst = AgentBrain(model=model or C.BRAIN_MODEL,
                                  mode=mode or C.BRAIN_MODE)
        self._reviewer_client = None
        if C.REVIEWER_ENABLED and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic
                self._reviewer_client = anthropic.Anthropic()
            except Exception:
                self._reviewer_client = None

    def decide(self, *, signal: Dict, indicators: Dict, risk_ctx: Dict,
               cmc_ctx: Dict, rules_summary: str,
               evidence_payload: Optional[Dict] = None) -> GatewayDecision:
        # ── pass 1: analyst (порт agent_brain) ──
        a: Decision = self.analyst.decide(
            signal=signal, indicators=indicators, risk_ctx=risk_ctx,
            cmc_ctx=cmc_ctx, rules_summary=rules_summary)
        if not a.enter:
            return GatewayDecision(False, 0.0, a.rationale, "(не дошло до ревью)",
                                   source=a.source)

        # ── pass 2: reviewer ──
        if self._reviewer_client is None:
            return GatewayDecision(True, min(1.0, a.size_factor), a.rationale,
                                   "reviewer off → детерминированный pass",
                                   source=f"{a.source}+fallback")
        try:
            payload = {
                "proposed_entry": {"signal": signal, "indicators": indicators,
                                   "analyst_size_factor": a.size_factor,
                                   "analyst_rationale": a.rationale},
                "risk_context": risk_ctx, "market_context": cmc_ctx,
                "rules": rules_summary,
                "evidence": evidence_payload or {},
            }
            msg = self._reviewer_client.messages.create(
                model=C.BRAIN_MODEL, max_tokens=400, system=_REVIEW_SYSTEM,
                tools=[_REVIEW_TOOL],
                tool_choice={"type": "tool", "name": "review_decision"},
                messages=[{"role": "user",
                           "content": json.dumps(payload, ensure_ascii=False,
                                                 default=str)}],
            )
            block = next(b for b in msg.content if b.type == "tool_use")
            verdict = block.input.get("verdict", "approve")
            notes = block.input.get("notes", "")
            if verdict == "veto":
                return GatewayDecision(False, 0.0, a.rationale, notes, "llm+llm")
            sf = a.size_factor
            if verdict == "downgrade":
                sf = min(sf, max(0.0, min(1.0, float(block.input.get("size_factor", 0.5)))))
            return GatewayDecision(True, min(1.0, sf), a.rationale, notes, "llm+llm")
        except Exception as e:
            return GatewayDecision(True, min(1.0, a.size_factor), a.rationale,
                                   f"reviewer error → pass ({type(e).__name__})",
                                   source=f"{a.source}+fallback")
