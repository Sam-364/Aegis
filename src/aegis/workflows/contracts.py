"""Typed payloads exchanged between the workflow and its activities (pydantic data converter)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from aegis.domain.flow import BudgetUsage

TASK_QUEUE = "aegis-incidents"


class IncidentWorkflowInput(BaseModel):
    incident_id: uuid.UUID
    approval_timeout_seconds: int = 900
    phase_timeout_seconds: int = 600
    max_phases: int = 14


class TriageResult(BaseModel):
    flow_name: str
    flow_version: str
    initial_phase: str
    severity: str
    max_remediation_attempts: int = 2
    active: bool = True


class RunPhaseInput(BaseModel):
    incident_id: uuid.UUID
    flow_name: str
    flow_version: str
    phase: str
    agent_run_id: uuid.UUID
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    workflow_id: str | None = None
    attempt: int = 1
    feedback: list[str] = Field(default_factory=list)
    refresh: bool = False  # re-observation: collect before judging the exit conditions


class PhaseResult(BaseModel):
    agent_run_id: uuid.UUID
    decision: str
    trigger: str | None = None
    next_phase: str | None = None
    termination: str | None = None
    action_plan_id: uuid.UUID | None = None
    usage: BudgetUsage
    summary: str = ""
    iterations: int = 0
    refresh_pending: bool = False


class PolicyOutcome(BaseModel):
    effect: str  # allow | deny | require_approval
    approval_id: uuid.UUID | None = None
    reason: str = ""
    matched_rule: str | None = None


class ApprovalSignal(BaseModel):
    approval_id: uuid.UUID
    approved: bool
    decided_by: str
    reason: str = ""


class ExecutionOutcome(BaseModel):
    status: str  # succeeded | failed | timed_out | denied | skipped_duplicate
    tool_execution_id: uuid.UUID | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    denial_code: str | None = None


class VerificationOutcome(BaseModel):
    status: str  # passed | failed | inconclusive
    summary: str
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)
    rollback_available: bool = False


class FinalizeInput(BaseModel):
    incident_id: uuid.UUID
    outcome: str  # resolved | escalated | failed | closed
    summary: str
    resolution: str = ""
    usage: BudgetUsage = Field(default_factory=BudgetUsage)


class IncidentWorkflowResult(BaseModel):
    incident_id: uuid.UUID
    outcome: str
    phases: list[str] = Field(default_factory=list)
    remediation_attempts: int = 0
    summary: str = ""


class WorkflowStatus(BaseModel):
    phase: str | None = None
    status: str = "running"
    awaiting_approval_id: uuid.UUID | None = None
    remediation_attempts: int = 0
    reobservations: int = 0
    phases: list[str] = Field(default_factory=list)
    cancelled: bool = False
