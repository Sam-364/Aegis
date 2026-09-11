"""Tool metadata, call requests and authorization decisions.

Implementation lives in ``aegis.tools``; this module holds only the shapes that cross layer
boundaries (they are persisted, audited and rendered by the UI).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Actor, Timestamped, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import (
    Environment,
    ExecutionStatus,
    PolicyEffect,
    RiskLevel,
    Severity,
    ToolCategory,
)
from aegis.domain.ids import IncidentId, ToolExecutionId, new_id


class RetryPolicy(ValueObject):
    max_attempts: int = Field(default=1, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.5, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)
    max_backoff_seconds: float = Field(default=10.0, ge=0)


class ToolSpec(ValueObject):
    """Serializable description of a tool (what the registry exposes to the API and UI)."""

    name: str
    version: str
    description: str
    category: ToolCategory
    risk: RiskLevel
    idempotent: bool
    timeout_seconds: float = 10.0
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    allowed_environments: frozenset[Environment] = Field(
        default_factory=lambda: frozenset(Environment)
    )
    min_severity: Severity | None = None  # tool only usable when incident is at least this severe
    enabled: bool = True
    arguments_schema: dict[str, Any] = Field(default_factory=dict)
    produces_evidence: bool = True
    verification_metrics: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class ToolCallRequest(ValueObject):
    """What the LLM (or the workflow) asks for. Nothing here is trusted."""

    id: uuid.UUID = Field(default_factory=new_id)
    incident_id: IncidentId
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    requested_by: Actor
    agent_run_id: uuid.UUID | None = None
    action_plan_id: uuid.UUID | None = None
    phase: str | None = None
    requested_at: datetime = Field(default_factory=utcnow)


class AuthorizationCheck(ValueObject):
    name: str
    passed: bool
    detail: str = ""


class AuthorizationDecision(ValueObject):
    """The ordered outcome of the authorization pipeline for one request."""

    request_id: uuid.UUID
    tool_name: str
    effect: PolicyEffect
    allowed: bool
    checks: tuple[AuthorizationCheck, ...]
    denial_code: str | None = None
    reason: str = ""
    matched_policy: str | None = None
    decided_at: datetime = Field(default_factory=utcnow)

    @property
    def failed_check(self) -> AuthorizationCheck | None:
        return next((c for c in self.checks if not c.passed), None)


class ToolExecutionRecord(Timestamped):
    """Persisted record of one tool execution (or refusal)."""

    id: ToolExecutionId = Field(default_factory=lambda: ToolExecutionId(new_id()))
    incident_id: IncidentId
    request_id: uuid.UUID
    tool_name: str
    tool_version: str
    category: ToolCategory
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    authorization: AuthorizationDecision
    agent_run_id: uuid.UUID | None = None
    action_plan_id: uuid.UUID | None = None
    phase: str | None = None
    attempt: int = 1
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    error: str | None = None
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    trace_id: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED
