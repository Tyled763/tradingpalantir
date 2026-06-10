# =========================
# intelligence/evidence.py — TradingPalantir
# EvidenceItem (§9 спека): нормализованный вывод любого скилла/источника.
# blocked/partial сохраняются честно — никогда не выдумываем данные.
# =========================
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# status: complete | partial | blocked | stale | error
# directional_impact: bullish | bearish | neutral | risk_on | risk_off | mixed | unknown
# risk_impact: increase_risk | reduce_risk | tighten_stop | hold | avoid | emergency | unknown


@dataclass
class EvidenceItem:
    source_skill: str
    pipeline: str
    category: str
    status: str = "complete"
    confidence: float = 0.5
    directional_impact: str = "unknown"
    risk_impact: str = "unknown"
    summary: str = ""
    asset_symbol: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def usable(self) -> bool:
        return self.status in ("complete", "partial")


class EvidencePack:
    """Набор evidence для одного решения (вход/выход/скоринг)."""

    def __init__(self, pipeline: str, symbol: Optional[str] = None):
        self.pipeline = pipeline
        self.symbol = symbol
        self.items: List[EvidenceItem] = []

    def add(self, item: EvidenceItem) -> "EvidencePack":
        self.items.append(item)
        return self

    def summaries(self) -> List[str]:
        return [f"[{i.source_skill}/{i.status}] {i.summary}" for i in self.items]

    def to_payload(self) -> Dict:
        """Компакт для LLM: без raw, только выводы."""
        return {
            "pipeline": self.pipeline,
            "symbol": self.symbol,
            "evidence": [
                {"skill": i.source_skill, "status": i.status,
                 "confidence": i.confidence, "impact": i.directional_impact,
                 "risk": i.risk_impact, "summary": i.summary,
                 "missing": i.missing_inputs}
                for i in self.items
            ],
        }
