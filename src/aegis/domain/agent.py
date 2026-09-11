"""Agent runs and steps: the durable, inspectable record of what the agent did."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Timestamped, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import AgentRunStatus, AgentStepKind, TerminationReason
from aegis.domain.flow import BudgetUsage, ExecutionBudget
from aegis.domain.ids import AgentRunId, AgentStepId, IncidentId, new_id


class AgentRun(Timestamped):
    id: AgentRunId = Field(default_factory=lambda: AgentRunId(new_id()))
    incident_id: IncidentId
    flow_name: str
    flow_version: str
    phase: str
    status: AgentRunStatus = AgentRunStatus.RUNNING
    budget: ExecutionBudget
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    model: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    termination_reason: TerminationReason | None = None
    summary: str = ""
    error: str | None = None
    steps_count: int = 0
    workflow_id: str | None = None
    attempt: int = 1


class AgentStep(ValueObject):
    id: AgentStepId = Field(default_factory=lambda: AgentStepId(new_id()))
    agent_run_id: AgentRunId
    incident_id: IncidentId
    seq: int
    phase: str
    node: str
    kind: AgentStepKind
    at: datetime = Field(default_factory=utcnow)
    title: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    tool_execution_id: uuid.UUID | None = None
    trace_id: str | None = None
