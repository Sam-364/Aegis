"""Incident aggregate, anomaly signals and the append-only incident timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Actor, Timestamped, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import Environment, IncidentStatus, Severity, SignalKind
from aegis.domain.ids import DEFAULT_TENANT, IncidentId, SignalId, TenantId, new_id


class AnomalySignal(ValueObject):
    """One detected anomaly on one (service, metric)."""

    id: SignalId = Field(default_factory=lambda: SignalId(new_id()))
    service: str
    metric: str
    kind: SignalKind
    observed_value: float
    baseline_value: float
    deviation_sigma: float
    detector: str
    detected_at: datetime
    window_seconds: int
    description: str = ""

    @property
    def magnitude(self) -> float:
        """Relative change vs baseline; guards division by zero."""
        if self.baseline_value == 0:
            return float("inf") if self.observed_value else 0.0
        return (self.observed_value - self.baseline_value) / abs(self.baseline_value)


class Incident(Timestamped):
    id: IncidentId = Field(default_factory=lambda: IncidentId(new_id()))
    tenant_id: TenantId = DEFAULT_TENANT
    environment: Environment = Environment.DEVELOPMENT
    number: int = 0  # human-facing INC-<number>, assigned by the repository
    title: str
    summary: str = ""
    severity: Severity
    status: IncidentStatus = IncidentStatus.DETECTED
    flow_name: str | None = None
    flow_version: str | None = None
    correlation_key: str = ""
    affected_services: list[str] = Field(default_factory=list)
    signals: list[AnomalySignal] = Field(default_factory=list)
    leading_hypothesis_id: uuid.UUID | None = None
    active_action_plan_id: uuid.UUID | None = None
    workflow_id: str | None = None
    detected_at: datetime = Field(default_factory=utcnow)
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    resolution_summary: str = ""
    root_cause_summary: str = ""
    remediation_attempts: int = 0
    version: int = 1  # optimistic concurrency

    @property
    def display_id(self) -> str:
        return f"INC-{self.number}" if self.number else f"INC-{str(self.id)[:8]}"

    @property
    def is_active(self) -> bool:
        return self.status.is_active

    def duration_seconds(self, now: datetime | None = None) -> float:
        end = self.resolved_at or self.closed_at or (now or utcnow())
        return max(0.0, (end - self.detected_at).total_seconds())

    def attach_signal(self, signal: AnomalySignal) -> None:
        if all(s.id != signal.id for s in self.signals):
            self.signals.append(signal)
        if signal.service not in self.affected_services:
            self.affected_services.append(signal.service)
        self.touch()


class IncidentEvent(ValueObject):
    """Append-only timeline entry. ``seq`` is assigned per incident by the repository and is
    the ordering key for SSE clients."""

    id: uuid.UUID = Field(default_factory=new_id)
    incident_id: IncidentId
    seq: int = 0
    type: str
    at: datetime = Field(default_factory=utcnow)
    actor: Actor
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
