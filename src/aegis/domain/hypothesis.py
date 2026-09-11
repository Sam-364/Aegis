"""Hypotheses and their runtime-computed scores.

The LLM proposes hypotheses referencing evidence ids. The runtime validates the references and
computes confidence. A hypothesis never carries an LLM-asserted confidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Timestamped, ValueObject
from aegis.domain.enums import HypothesisCategory, HypothesisStatus
from aegis.domain.ids import HypothesisId, IncidentId, new_id


class HypothesisScore(ValueObject):
    """Explainable confidence breakdown. Each component is in [0, 1]."""

    evidence_strength: float = 0.0
    temporal_alignment: float = 0.0
    dependency_alignment: float = 0.0
    historical_similarity: float = 0.0
    contradiction_penalty: float = 0.0
    validation_bonus: float = 0.0
    total: float = 0.0
    explanation: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SuggestedTest(ValueObject):
    """A diagnostic the agent believes would confirm or refute the hypothesis."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expectation: str
    would_confirm: bool = True


class HypothesisTest(ValueObject):
    id: uuid.UUID = Field(default_factory=new_id)
    hypothesis_id: HypothesisId
    tool_execution_id: uuid.UUID | None = None
    tool_name: str
    expectation: str
    outcome: str  # confirmed | refuted | inconclusive
    detail: str = ""
    at: datetime


class Hypothesis(Timestamped):
    id: HypothesisId = Field(default_factory=lambda: HypothesisId(new_id()))
    incident_id: IncidentId
    statement: str
    category: HypothesisCategory = HypothesisCategory.UNKNOWN
    suspected_root_cause_service: str | None = None
    mechanism: str = ""
    affected_services: list[str] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    score: HypothesisScore = Field(default_factory=HypothesisScore)
    supporting_evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    contradicting_evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    suggested_tests: list[SuggestedTest] = Field(default_factory=list)
    tests: list[HypothesisTest] = Field(default_factory=list)
    proposed_by: str = "llm"  # llm | deterministic | memory
    agent_run_id: uuid.UUID | None = None
    version: int = 1

    def confirmed_tests(self) -> int:
        return sum(1 for t in self.tests if t.outcome == "confirmed")

    def refuted_tests(self) -> int:
        return sum(1 for t in self.tests if t.outcome == "refuted")
