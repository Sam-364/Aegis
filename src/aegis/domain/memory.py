"""Structured incident memory. Retrieved memories are evidence, never truth."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Timestamped, ValueObject
from aegis.domain.enums import Severity
from aegis.domain.ids import IncidentId, MemoryId, new_id


class IncidentMemory(Timestamped):
    id: MemoryId = Field(default_factory=lambda: MemoryId(new_id()))
    incident_id: IncidentId
    incident_number: int = 0
    title: str
    severity: Severity
    symptoms: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    root_cause: str
    root_cause_service: str | None = None
    root_cause_category: str = "unknown"
    evidence_summary: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    resolution: str
    verification_summary: str = ""
    duration_seconds: float = 0.0
    outcome: str = "resolved"  # resolved | escalated | false_positive
    lessons: list[str] = Field(default_factory=list)
    embedding_text: str = ""
    embedding: list[float] | None = None
    embedding_model: str | None = None
    resolved_at: datetime | None = None

    def to_embedding_text(self) -> str:
        parts = [
            f"Title: {self.title}",
            f"Severity: {self.severity.value}",
            f"Symptoms: {'; '.join(self.symptoms)}",
            f"Affected services: {', '.join(self.affected_services)}",
            f"Root cause: {self.root_cause}",
            f"Root cause service: {self.root_cause_service or 'unknown'}",
            f"Category: {self.root_cause_category}",
            f"Resolution: {self.resolution}",
        ]
        return "\n".join(parts)


class MemoryMatch(ValueObject):
    memory: IncidentMemory
    similarity: float
    matched_on: str = "embedding"


class SimilarIncidentQuery(ValueObject):
    text: str
    affected_services: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=25)
    exclude_incident_id: uuid.UUID | None = None
