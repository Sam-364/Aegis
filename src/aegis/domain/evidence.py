"""Evidence graph primitives.

Evidence is structured, typed data collected by tools, the detector or memory retrieval. The
agent reasons over the graph; it does not reason over a transcript.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from aegis.domain.base import Timestamped, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import EvidenceKind, GraphNodeKind, RelationKind
from aegis.domain.ids import EvidenceId, IncidentId, new_id


class Evidence(Timestamped):
    id: EvidenceId = Field(default_factory=lambda: EvidenceId(new_id()))
    incident_id: IncidentId
    kind: EvidenceKind
    source: str  # tool name, "detector", "memory", "verification"
    service: str | None = None
    title: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    observed_at: datetime = Field(default_factory=utcnow)
    tool_execution_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    phase: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("title", "summary")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class EvidenceRelation(ValueObject):
    """Directed edge in the evidence graph. Endpoints may be evidence, hypotheses, services or
    actions; ``*_kind`` disambiguates the id space."""

    id: uuid.UUID = Field(default_factory=new_id)
    incident_id: IncidentId
    from_id: str
    from_kind: GraphNodeKind
    to_id: str
    to_kind: GraphNodeKind
    kind: RelationKind
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = "runtime"


class EvidenceGraph(ValueObject):
    """A read-model of the graph for one incident."""

    incident_id: IncidentId
    evidence: tuple[Evidence, ...] = ()
    relations: tuple[EvidenceRelation, ...] = ()

    def by_id(self, evidence_id: uuid.UUID) -> Evidence | None:
        return next((e for e in self.evidence if e.id == evidence_id), None)

    def for_service(self, service: str) -> list[Evidence]:
        return [e for e in self.evidence if e.service == service]

    def of_kind(self, kind: EvidenceKind) -> list[Evidence]:
        return [e for e in self.evidence if e.kind == kind]

    def relations_from(self, node_id: str) -> list[EvidenceRelation]:
        return [r for r in self.relations if r.from_id == node_id]

    def relations_to(self, node_id: str) -> list[EvidenceRelation]:
        return [r for r in self.relations if r.to_id == node_id]
