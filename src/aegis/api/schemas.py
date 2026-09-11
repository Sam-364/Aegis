"""API request/response schemas. Domain models are exposed directly where they are already the
right shape; wrappers exist for pagination, compact lists and commands."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from aegis.domain.enums import IncidentStatus, Severity
from aegis.domain.incident import Incident


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class IncidentSummary(BaseModel):
    id: uuid.UUID
    number: int
    display_id: str
    title: str
    severity: Severity
    status: IncidentStatus
    affected_services: list[str]
    flow_name: str | None
    detected_at: str
    resolved_at: str | None
    duration_seconds: float
    leading_hypothesis_id: uuid.UUID | None
    active_action_plan_id: uuid.UUID | None
    root_cause_summary: str
    signal_count: int
    workflow_id: str | None

    @classmethod
    def from_incident(cls, i: Incident) -> IncidentSummary:
        return cls(
            id=i.id,
            number=i.number,
            display_id=i.display_id,
            title=i.title,
            severity=i.severity,
            status=i.status,
            affected_services=i.affected_services,
            flow_name=i.flow_name,
            detected_at=i.detected_at.isoformat(),
            resolved_at=i.resolved_at.isoformat() if i.resolved_at else None,
            duration_seconds=i.duration_seconds(),
            leading_hypothesis_id=i.leading_hypothesis_id,
            active_action_plan_id=i.active_action_plan_id,
            root_cause_summary=i.root_cause_summary,
            signal_count=len(i.signals),
            workflow_id=i.workflow_id,
        )


class ApprovalDecision(BaseModel):
    reason: str = Field(default="", max_length=500)


class StatusChange(BaseModel):
    reason: str = Field(default="", max_length=500)


class InjectFault(BaseModel):
    scenario_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class SimulationControl(BaseModel):
    seconds: float | None = Field(default=None, gt=0, le=3600)
    seed: int | None = None


class StatsResponse(BaseModel):
    by_status: dict[str, int]
    total: int
    active: int
    resolved: int
    mttr_seconds: float | None
    pending_approvals: int
    memories: int


class Principal(BaseModel):
    id: str
    display_name: str | None
    roles: list[str]
    auth_mode: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, Any]
