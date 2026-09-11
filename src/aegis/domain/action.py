"""Action plans, rollback plans, verification specs and executions.

An ``ActionPlan`` is a *proposal*. It becomes executable only after the policy engine and, if
required, a human have authorized it. Execution is idempotent by construction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from aegis.domain.base import Timestamped, ValueObject
from aegis.domain.enums import ActionPlanStatus, ExecutionStatus, RiskLevel, VerificationStatus
from aegis.domain.ids import ActionExecutionId, ActionPlanId, IncidentId, new_id

Comparator = Literal["lt", "lte", "gt", "gte"]


class VerificationCondition(ValueObject):
    """A measurable condition that must hold after remediation.

    Exactly one of ``target`` (absolute) or ``max_ratio_to_baseline`` (relative) is used.
    """

    metric: str
    service: str
    comparator: Comparator = "lte"
    target: float | None = None
    max_ratio_to_baseline: float | None = None
    description: str = ""

    def evaluate(self, observed: float, baseline: float | None) -> tuple[bool, str]:
        if self.max_ratio_to_baseline is not None:
            if baseline is None:
                return False, "no baseline available"
            threshold = baseline * self.max_ratio_to_baseline
            ok = observed <= threshold
            return ok, (
                f"{self.service}.{self.metric}={observed:.3g} vs "
                f"{self.max_ratio_to_baseline:g}x baseline({baseline:.3g})={threshold:.3g}"
            )
        if self.target is None:
            return False, "condition has neither target nor ratio"
        ops = {
            "lt": observed < self.target,
            "lte": observed <= self.target,
            "gt": observed > self.target,
            "gte": observed >= self.target,
        }
        return ops[self.comparator], (
            f"{self.service}.{self.metric}={observed:.3g} {self.comparator} {self.target:g}"
        )


class VerificationSpec(ValueObject):
    conditions: tuple[VerificationCondition, ...]
    stabilization_seconds: int = 30
    timeout_seconds: int = 180
    require_all: bool = True


class VerificationResult(ValueObject):
    status: VerificationStatus
    checked_at: datetime
    condition_results: tuple[dict[str, Any], ...] = ()
    summary: str = ""
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)


class RollbackPlan(ValueObject):
    available: bool
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


def compute_idempotency_key(
    incident_id: uuid.UUID, tool_name: str, arguments: dict[str, Any], scope: str = "action"
) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{scope}|{incident_id}|{tool_name}|{canonical}".encode()).hexdigest()
    return digest[:32]


class ActionPlan(Timestamped):
    id: ActionPlanId = Field(default_factory=lambda: ActionPlanId(new_id()))
    incident_id: IncidentId
    hypothesis_id: uuid.UUID | None = None
    tool_name: str
    tool_version: str = "1"
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str
    expected_effect: str
    risk: RiskLevel = RiskLevel.MEDIUM
    rollback: RollbackPlan = Field(default_factory=lambda: RollbackPlan(available=False))
    verification: VerificationSpec
    timeout_seconds: int = 120
    status: ActionPlanStatus = ActionPlanStatus.PROPOSED
    idempotency_key: str = ""
    proposed_by: str = "llm"
    agent_run_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    policy_decision: dict[str, Any] | None = None
    attempt: int = 1
    verification_result: VerificationResult | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.idempotency_key:
            self.idempotency_key = compute_idempotency_key(
                self.incident_id, self.tool_name, self.arguments, scope=f"attempt:{self.attempt}"
            )


class ActionExecution(Timestamped):
    id: ActionExecutionId = Field(default_factory=lambda: ActionExecutionId(new_id()))
    action_plan_id: ActionPlanId
    incident_id: IncidentId
    tool_execution_id: uuid.UUID | None = None
    idempotency_key: str
    attempt: int = 1
    status: ExecutionStatus = ExecutionStatus.PENDING
    is_rollback: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
