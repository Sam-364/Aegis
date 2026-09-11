"""The authorization pipeline.

Every tool call, whether requested by the LLM inside the reasoning loop or by the workflow for a
remediation, passes through the same ordered checks. The pipeline never raises: it returns an
``AuthorizationDecision`` with the full check trace so denials are as inspectable as approvals.
``raise_for`` converts a denial into the matching typed exception when a caller prefers that.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aegis.domain.action import compute_idempotency_key
from aegis.domain.approval import ApprovalRequest
from aegis.domain.base import Actor
from aegis.domain.enums import (
    ApprovalStatus,
    Environment,
    ExecutionStatus,
    PolicyEffect,
    RiskLevel,
    Role,
    ToolCategory,
)
from aegis.domain.errors import (
    ApprovalRequired,
    DuplicateExecution,
    ExecutionBudgetExceeded,
    FlowViolation,
    IncidentInactive,
    InvalidToolArguments,
    PhaseViolation,
    PolicyViolation,
    RiskCeilingExceeded,
    ToolAuthorizationError,
    ToolDisabled,
    ToolNotAllowed,
    ToolNotRegistered,
)
from aegis.domain.flow import BudgetUsage, ExecutionBudget, FlowPack, FlowPhase
from aegis.domain.incident import Incident
from aegis.domain.policy import PolicyContext, PolicyDecision
from aegis.domain.tool import AuthorizationCheck, AuthorizationDecision, ToolCallRequest
from aegis.policy.engine import PolicyEngine
from aegis.ports.repositories import ToolExecutionRepository
from aegis.tools.registry import ToolRegistry

# How long after a human decision a mutation may still execute. Long enough for a
# verification window and its rollback, short enough that an old approval cannot
# authorize a fresh change.
APPROVAL_EXECUTION_GRACE = timedelta(hours=1)

DENIAL_EXCEPTIONS: dict[str, type[ToolAuthorizationError]] = {
    "tool_not_registered": ToolNotRegistered,
    "tool_disabled": ToolDisabled,
    "incident_inactive": IncidentInactive,
    "flow_violation": FlowViolation,
    "phase_violation": PhaseViolation,
    "tool_not_allowed": ToolNotAllowed,
    "risk_ceiling_exceeded": RiskCeilingExceeded,
    "policy_violation": PolicyViolation,
    "approval_required": ApprovalRequired,
    "invalid_tool_arguments": InvalidToolArguments,
    "duplicate_execution": DuplicateExecution,
    "execution_budget_exceeded": ExecutionBudgetExceeded,
}

REQUIRED_ROLE: dict[ToolCategory, Role] = {
    ToolCategory.READ_ONLY: Role.VIEWER,
    ToolCategory.DIAGNOSTIC: Role.OPERATOR,
    ToolCategory.MUTATING: Role.OPERATOR,
    ToolCategory.DANGEROUS: Role.ADMIN,
}


class _DenialError(Exception):
    def __init__(self, code: str, detail: str, effect: PolicyEffect = PolicyEffect.DENY) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.effect = effect


class ToolAuthorizer:
    def __init__(
        self, registry: ToolRegistry, policy: PolicyEngine, *, environment: Environment
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.environment = environment

    async def authorize(  # noqa: PLR0912, PLR0915 - a 14-step pipeline is 14 branches
        self,
        request: ToolCallRequest,
        *,
        incident: Incident,
        flow: FlowPack,
        phase: FlowPhase,
        budget: ExecutionBudget,
        usage: BudgetUsage,
        ledger: ToolExecutionRepository,
        known_components: frozenset[str] = frozenset(),
        in_agent_loop: bool = True,
        approval: ApprovalRequest | None = None,
        now: datetime | None = None,
    ) -> AuthorizationDecision:
        checks: list[AuthorizationCheck] = []
        policy_decision: PolicyDecision | None = None

        def ok(name: str, detail: str) -> None:
            checks.append(AuthorizationCheck(name=name, passed=True, detail=detail))

        try:
            # 1. registered
            if not self.registry.has(request.tool_name):
                raise _DenialError(
                    "tool_not_registered", f"'{request.tool_name}' is not a registered tool"
                )
            defn = self.registry.get(request.tool_name)
            spec = defn.spec
            ok("tool_registered", f"{spec.ref} ({spec.category.value}, risk {spec.risk.value})")

            # 2. enabled
            if not self.registry.is_enabled(request.tool_name):
                raise _DenialError("tool_disabled", f"{spec.name} is disabled")
            ok("tool_enabled", "enabled")

            # 3. incident active
            if not incident.status.is_active:
                raise _DenialError(
                    "incident_inactive",
                    f"incident is {incident.status.value}; no further tool execution",
                )
            ok("incident_active", f"incident {incident.display_id} is {incident.status.value}")

            # 4. flow allows
            if request.tool_name not in flow.all_tools:
                raise _DenialError(
                    "flow_violation", f"flow {flow.ref} does not include {spec.name}"
                )
            ok("flow_allows", f"{spec.name} is part of flow {flow.ref}")

            # 5. phase allows
            if in_agent_loop:
                if request.tool_name not in phase.allowed_tools:
                    raise _DenialError(
                        "phase_violation",
                        f"phase '{phase.name}' allows {sorted(phase.allowed_tools)}, "
                        f"not {spec.name}",
                    )
                ok("phase_allows", f"phase '{phase.name}' lists {spec.name}")
            else:
                if request.tool_name not in flow.remediation_tools and spec.category.is_mutation:
                    raise _DenialError(
                        "phase_violation",
                        f"{spec.name} is not a remediation tool of flow {flow.ref}",
                    )
                ok("phase_allows", "workflow execution outside the agent loop")

            # 6. category permitted in this execution context
            if spec.category is ToolCategory.DANGEROUS:
                raise _DenialError("tool_not_allowed", "dangerous tools are never executable")
            if in_agent_loop and spec.category.is_mutation:
                raise _DenialError(
                    "tool_not_allowed",
                    "mutating tools cannot run inside the agent loop; the agent must "
                    "propose an action plan instead",
                )
            ok(
                "category_permitted",
                f"{spec.category.value} permitted "
                f"{'inside the agent loop' if in_agent_loop else 'via workflow'}",
            )

            # 7. environment
            if spec.category.is_mutation and not known_components:
                raise _DenialError(
                    "invalid_tool_arguments",
                    "the topology is unknown, so a mutation target cannot be checked",
                )
            if self.environment not in spec.allowed_environments:
                raise _DenialError(
                    "tool_not_allowed", f"{spec.name} is not permitted in {self.environment.value}"
                )
            ok("environment_allows", f"permitted in {self.environment.value}")

            # 8. severity
            if spec.min_severity is not None and not incident.severity.is_at_least(
                spec.min_severity
            ):
                raise _DenialError(
                    "tool_not_allowed",
                    f"{spec.name} requires at least {spec.min_severity.value}; incident is "
                    f"{incident.severity.value}",
                )
            ok(
                "severity_allows",
                f"incident severity {incident.severity.value} permits {spec.name}",
            )

            # 9. actor permitted
            required = REQUIRED_ROLE[spec.category]
            if not request.requested_by.has_role(required):
                raise _DenialError(
                    "tool_not_allowed",
                    f"actor {request.requested_by.id} lacks role {required.value}",
                )
            ok("actor_permitted", f"{request.requested_by.id} has role {required.value}")

            # 10. risk within ceiling
            ceiling = phase.risk_ceiling if in_agent_loop else flow.remediation_risk_ceiling
            if spec.risk.exceeds(ceiling):
                raise _DenialError(
                    "risk_ceiling_exceeded",
                    f"tool risk {spec.risk.value} exceeds ceiling {ceiling.value}",
                )
            ok("risk_within_ceiling", f"risk {spec.risk.value} <= ceiling {ceiling.value}")

            # 11. policy
            ctx = PolicyContext(
                tool_name=spec.name,
                tool_category=spec.category,
                tool_risk=spec.risk,
                environment=self.environment,
                severity=incident.severity,
                flow_name=flow.name,
                phase=phase.name,
                actor=request.requested_by,
                incident_id=incident.id,
                in_agent_loop=in_agent_loop,
                arguments=request.arguments,
                remediation_attempt=incident.remediation_attempts,
                tenant_id=incident.tenant_id,
            )
            policy_decision = self.policy.evaluate(ctx)
            if policy_decision.effect is PolicyEffect.DENY:
                raise _DenialError("policy_violation", policy_decision.reason)
            if policy_decision.effect is PolicyEffect.REQUIRE_APPROVAL:
                granted = self._approval_granted(approval, request, now or request.requested_at)
                if granted is None:
                    raise _DenialError(
                        "approval_required",
                        policy_decision.reason,
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ok(
                    "policy_effect",
                    f"approval required by rule "
                    f"'{policy_decision.matched_rule}'; granted: {granted}",
                )
            else:
                ok("policy_effect", f"allowed by rule '{policy_decision.matched_rule}'")

            # 12. arguments
            try:
                defn.parse_args(request.arguments, known_components=known_components)
            except InvalidToolArguments as exc:
                raise _DenialError("invalid_tool_arguments", exc.message) from exc
            except Exception as exc:
                raise _DenialError(
                    "invalid_tool_arguments",
                    f"arguments could not be validated: {type(exc).__name__}",
                ) from exc
            ok("arguments_valid", f"{len(request.arguments)} argument(s) validated")

            # 13. idempotency (concurrent duplicate)
            key = idempotency_key_for(request, spec.category)
            existing = await ledger.find_by_key(key)
            if existing is not None and existing.status is ExecutionStatus.RUNNING:
                raise _DenialError(
                    "duplicate_execution", f"an execution with key {key} is already running"
                )
            ok(
                "idempotency",
                f"key {key[:12]}… "
                + (
                    "has a prior completed execution; result will be reused"
                    if existing
                    else "is new"
                ),
            )

            # 14. budget
            exceeded = usage.exceeded(budget)
            if exceeded:
                raise _DenialError("execution_budget_exceeded", "; ".join(exceeded))
            ok(
                "budget_available",
                f"tool_calls {usage.tool_calls}/{budget.max_tool_calls}, "
                f"iterations {usage.iterations}/{budget.max_iterations}",
            )
        except _DenialError as deny:
            checks.append(
                AuthorizationCheck(
                    name=_check_name_for(deny.code, len(checks)), passed=False, detail=deny.detail
                )
            )
            return AuthorizationDecision(
                request_id=request.id,
                tool_name=request.tool_name,
                effect=deny.effect,
                allowed=False,
                checks=tuple(checks),
                denial_code=deny.code,
                reason=deny.detail,
                matched_policy=policy_decision.matched_rule if policy_decision else None,
                decided_at=now or request.requested_at,
            )

        return AuthorizationDecision(
            request_id=request.id,
            tool_name=request.tool_name,
            effect=PolicyEffect.ALLOW,
            allowed=True,
            checks=tuple(checks),
            reason="all checks passed",
            matched_policy=policy_decision.matched_rule if policy_decision else None,
            decided_at=now or request.requested_at,
        )

    @staticmethod
    def _approval_granted(
        approval: ApprovalRequest | None, request: ToolCallRequest, now: datetime
    ) -> str | None:
        """Decide whether this request is the thing a human actually approved.

        A plan id alone is not enough. The approval must belong to the same incident, must have
        been decided recently, and must name the tool and arguments about to run — either the
        approved action itself or the rollback that was presented alongside it. Without those
        bindings one approval could authorize a different action, on a different incident, an
        arbitrary time later.
        """
        if approval is None or approval.status is not ApprovalStatus.APPROVED:
            return None
        if request.action_plan_id is None or approval.action_plan_id != request.action_plan_id:
            return None
        if approval.incident_id != request.incident_id:
            return None
        if approval.decided_at is None or now - approval.decided_at > APPROVAL_EXECUTION_GRACE:
            return None
        context = approval.context
        approved: list[tuple[Any, Any]] = [
            (context.get("tool_name"), context.get("arguments") or {})
        ]
        rollback = context.get("rollback")
        if isinstance(rollback, dict) and rollback.get("tool_name"):
            approved.append((rollback.get("tool_name"), rollback.get("arguments") or {}))
        for tool_name, arguments in approved:
            if tool_name == request.tool_name and arguments == request.arguments:
                return f"{approval.decided_by} ({approval.id})"
        return None


_CHECK_NAMES = [
    "tool_registered",
    "tool_enabled",
    "incident_active",
    "flow_allows",
    "phase_allows",
    "category_permitted",
    "environment_allows",
    "severity_allows",
    "actor_permitted",
    "risk_within_ceiling",
    "policy_effect",
    "arguments_valid",
    "idempotency",
    "budget_available",
]


def _check_name_for(code: str, index: int) -> str:
    return _CHECK_NAMES[index] if index < len(_CHECK_NAMES) else code


def idempotency_key_for(request: ToolCallRequest, category: ToolCategory) -> str:
    """Mutations are keyed by action plan (exactly-once per attempt); reads are keyed per request
    because re-reading fresh data is harmless and often desired."""
    if category.is_mutation and request.action_plan_id is not None:
        return compute_idempotency_key(
            request.incident_id,
            request.tool_name,
            request.arguments,
            scope=f"plan:{request.action_plan_id}",
        )
    return compute_idempotency_key(
        request.incident_id, request.tool_name, request.arguments, scope=f"request:{request.id}"
    )


def raise_for(decision: AuthorizationDecision) -> None:
    if decision.allowed:
        return
    exc_type = DENIAL_EXCEPTIONS.get(decision.denial_code or "", ToolAuthorizationError)
    raise exc_type(
        decision.reason,
        details={
            "tool": decision.tool_name,
            "denial_code": decision.denial_code,
            "failed_check": decision.failed_check.name if decision.failed_check else None,
        },
    )


def actor_for_workflow(workflow_id: str) -> Actor:
    return Actor.workflow(workflow_id)


__all__ = [
    "DENIAL_EXCEPTIONS",
    "RiskLevel",
    "ToolAuthorizer",
    "actor_for_workflow",
    "idempotency_key_for",
    "raise_for",
]
