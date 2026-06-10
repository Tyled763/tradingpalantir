# =========================
# agent_brain.py — BNB HACK Trading Agent
# LLM-слой Claude поверх детерминированного движка (нарратив "autonomous AI agent").
#
# Ядро решений — детерминированная стратегия (engine.py) + риск-гейты (risk.py).
# Brain НЕ создаёт сделки и НЕ повышает риск. Он только:
#   - подтверждает вход, или ветует его (skip),
#   - может СРЕЗАТЬ размер (size_factor ∈ [0,1]),
#   - даёт человекочитаемое обоснование (журнал/демо/заявка).
#
# Capability-gating (как X402Signer в bnbagent): что бы LLM ни вернул,
# size_factor клампится в [0,1], decision ∈ {enter,skip}. Риск-капы из risk.py
# применяются ПОСЛЕ и независимо. Нет ключа/SDK/ответа → детерминированный
# fallback (decision=enter, size_factor=1.0) — торговля не блокируется.
# =========================
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Decision:
    decision: str          # "enter" | "skip"
    size_factor: float     # 0..1 — доля разрешённого риска
    rationale: str
    source: str            # "llm" | "deterministic"

    @property
    def enter(self) -> bool:
        return self.decision == "enter"


_DECISION_TOOL = {
    "name": "trade_decision",
    "description": (
        "Record the final decision for a deterministic LONG spot signal. "
        "You may only confirm (enter) or veto (skip), and optionally REDUCE "
        "size via size_factor. You cannot increase risk."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["enter", "skip"]},
            "size_factor": {
                "type": "number",
                "description": "Fraction of the already-approved risk to use, 0.0–1.0. "
                               "1.0 = full approved size; lower = more conservative.",
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences explaining the decision for the trade journal.",
            },
        },
        "required": ["decision", "size_factor", "rationale"],
    },
}

_SYSTEM_BASE = (
    "You are the risk-aware supervisor of an autonomous crypto spot-trading agent on "
    "BNB Chain. A deterministic strategy (FVG + VWAP + multi-timeframe EMA) has ALREADY "
    "produced a valid LONG signal that passed hard risk caps. Your job is NOT to find "
    "trades — it is to act as a final sanity gate using market context: 24h move, trading "
    "volume, recent news headlines (sentiment), social buzz, and the macro liquidity regime. "
    "Confirm structurally sound setups; veto likely traps; reduce size when context is shaky. "
    "You can ONLY confirm (enter) or veto (skip) and optionally REDUCE size (size_factor ≤ 1) — "
    "you can never increase risk. Always answer by calling the trade_decision tool."
)

_SYSTEM_MODE = {
    "conservative": (
        " MODE: CONSERVATIVE. Protecting drawdown matters far more than catching every move. "
        "Veto readily: skip on negative/scary recent news, on an already-exhausted 24h pump, "
        "when fighting a clearly weak or risk_off macro regime, or when news/social give no "
        "positive support. When in doubt, SKIP or cut size hard (0.3–0.6). Only allow full "
        "size (≈1.0) on clean setups with neutral/positive news and a non-risk_off regime."
    ),
    "balanced": (
        " MODE: BALANCED. Trust the deterministic signal by default; veto only on clear red "
        "flags (very negative news, blatant exhaustion, risk_off + weak context). Otherwise "
        "confirm, trimming size moderately (0.6–1.0) when context is mixed."
    ),
    "permissive": (
        " MODE: PERMISSIVE. Confirm the strategy signal unless there is an unambiguous, severe "
        "red flag. Rarely veto; keep size near 1.0 unless context is clearly adverse."
    ),
}


def _system_for(mode: str) -> str:
    return _SYSTEM_BASE + _SYSTEM_MODE.get(mode, _SYSTEM_MODE["conservative"])


class AgentBrain:
    """
    LLM-надзиратель. Если ключа/SDK нет — прозрачный детерминированный fallback.
    model: claude-sonnet-4-6 (быстро) | claude-opus-4-8 (качество).
    """

    def __init__(self, model: str = "claude-opus-4-8", mode: str = "conservative",
                 api_key: Optional[str] = None, max_tokens: int = 500):
        self.model = model
        self.mode = mode
        self.system = _system_for(mode)
        self.max_tokens = max_tokens
        self._client = None
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=key)
            except Exception:
                self._client = None

    @property
    def active(self) -> bool:
        return self._client is not None

    def decide(self, *, signal: Dict, indicators: Dict, risk_ctx: Dict,
               cmc_ctx: Dict, rules_summary: str) -> Decision:
        """Возвращает Decision. При недоступности LLM — детерминированный fallback."""
        if self._client is None:
            return Decision("enter", 1.0,
                            "Deterministic mode (no LLM): strategy + risk gates only.",
                            "deterministic")
        try:
            return self._decide_llm(signal, indicators, risk_ctx, cmc_ctx, rules_summary)
        except Exception as e:
            # любая ошибка LLM не должна ронять торговлю — fallback
            return Decision("enter", 1.0,
                            f"LLM error ({type(e).__name__}); deterministic fallback.",
                            "deterministic")

    def _decide_llm(self, signal, indicators, risk_ctx, cmc_ctx, rules_summary) -> Decision:
        payload = {
            "signal": signal,                 # {type, direction}
            "indicators": indicators,         # close/ema/vwap/fractal_stop/tp/entry...
            "risk_context": risk_ctx,         # approved_risk_usdt, open_positions, dd_pct...
            "market_context_cmc": cmc_ctx,    # price/change_24h/global metrics
            "user_rules": rules_summary,
        }
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=[_DECISION_TOOL],
            tool_choice={"type": "tool", "name": "trade_decision"},
            messages=[{
                "role": "user",
                "content": (
                    "Evaluate this already-valid LONG spot signal and decide.\n\n"
                    f"```json\n{json.dumps(payload, default=str, indent=2)}\n```"
                ),
            }],
        )
        tool_input = None
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "trade_decision":
                tool_input = block.input
                break
        if not tool_input:
            return Decision("enter", 1.0, "LLM returned no tool call; fallback.", "deterministic")

        decision = "skip" if str(tool_input.get("decision")) == "skip" else "enter"
        sf = tool_input.get("size_factor", 1.0)
        try:
            sf = float(sf)
        except (TypeError, ValueError):
            sf = 1.0
        sf = max(0.0, min(1.0, sf))          # GATING: только в [0,1], не повышает риск
        rationale = str(tool_input.get("rationale", ""))[:500] or "(no rationale)"
        return Decision(decision, sf, rationale, "llm")
