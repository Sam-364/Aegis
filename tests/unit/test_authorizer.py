from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from aegis.domain.approval import ApprovalRequest
from aegis.domain.base import Actor
from aegis.domain.enums import (
    ApprovalStatus,
    Environment,
    ExecutionStatus,
    IncidentStatus,
    PolicyEffect,
    RiskLevel,
    Role,
    Severity,
    ToolCategory,
)
from aegis.domain.errors import ApprovalRequired, PhaseViolation, ToolNotRegistered
from aegis.domain.flow import BudgetUsage, ExecutionBudget
from aegis.domain.tool import ToolExecutionRecord
from aegis.tools.authorizer import idempotency_key_for, raise_for
from tests.helpers import build_runtime, human

CHECKS = [
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


@pytest.fixture
def rt():  # type: ignore[no-untyped-def]
    return build_runtime(warmup=30)


async def authorize(
    rt, req, ctx, *, in_loop=True, approval=None, budget=None, usage=None, now=None
):  # type: ignore[no-untyped-def]
    return await rt.authorizer.authorize(
        req,
        incident=ctx.incident,
        flow=ctx.flow,
        phase=ctx.phase,
        budget=budget or ExecutionBudget(),
        usage=usage or BudgetUsage(),
        ledger=rt.uow.tool_executions,
        known_components=ctx.known_components,
        in_agent_loop=in_loop,
        approval=approval,
        now=now,
    )


async def test_full_pass_records_all_fourteen_checks(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    ctx = rt.ctx(inc)
    d = await authorize(
        rt,
        rt.request(inc, "get_metrics", {"service": "api-gateway", "metric": "latency_p95_ms"}),
        ctx,
    )
    assert d.allowed and d.effect is PolicyEffect.ALLOW
    assert [c.name for c in d.checks] == CHECKS
    assert all(c.passed for c in d.checks)
    assert d.matched_policy == "allow-read-only"


@pytest.mark.parametrize(
    "tool,args,phase,code,failed_check",
    [
        ("teleport_pods", {}, "investigate", "tool_not_registered", "tool_registered"),
        (
            "run_cache_diagnostic",
            {"component": "redis"},
            "investigate",
            "phase_violation",
            "phase_allows",
        ),
        (
            "restart_service",
            {"service": "order-service"},
            "remediate",
            "flow_violation",
            "flow_allows",
        ),
        (
            "get_metrics",
            {"service": "api-gateway"},
            "investigate",
            "invalid_tool_arguments",
            "arguments_valid",
        ),
        (
            "get_metrics",
            {"service": "api-gateway", "metric": "x", "window_seconds": 5},
            "investigate",
            "invalid_tool_arguments",
            "arguments_valid",
        ),
        (
            "get_metrics",
            {"service": "not-a-service", "metric": "latency_p95_ms"},
            "investigate",
            "invalid_tool_arguments",
            "arguments_valid",
        ),
        (
            "get_logs",
            {"service": "api-gateway", "level": "WARN; rm -rf /"},
            "investigate",
            "invalid_tool_arguments",
            "arguments_valid",
        ),
    ],
)
async def test_denials_stop_at_the_failing_check(rt, tool, args, phase, code, failed_check) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    flow = rt.flows.get("incident-investigation")
    if tool == "restart_service":
        # a flow whose remediation tools exclude restart
        flow = flow.model_copy(update={"remediation_tools": frozenset({"scale_service"})})
    ctx = rt.ctx(inc, flow=flow, phase=phase)
    d = await authorize(rt, rt.request(inc, tool, args), ctx, in_loop=(tool != "restart_service"))
    assert not d.allowed
    assert d.denial_code == code, d.reason
    assert d.failed_check is not None and d.failed_check.name == failed_check
    assert [c.name for c in d.checks] == CHECKS[: CHECKS.index(failed_check) + 1]
    with pytest.raises(Exception) as exc:
        raise_for(d)
    assert getattr(exc.value, "code", None) == code


async def test_mutating_tool_denied_inside_agent_loop_before_policy(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    ctx = rt.ctx(inc, phase="remediate")
    flow = ctx.flow.model_copy(
        update={
            "phases": tuple(
                p.model_copy(update={"allowed_tools": p.allowed_tools | {"restart_service"}})
                if p.name == "remediate"
                else p
                for p in ctx.flow.phases
            )
        }
    )
    ctx = rt.ctx(inc, flow=flow, phase="remediate")
    d = await authorize(rt, rt.request(inc, "restart_service", {"service": "order-service"}), ctx)
    assert d.denial_code == "tool_not_allowed" and d.failed_check.name == "category_permitted"  # type: ignore[union-attr]


async def test_dangerous_tools_are_always_denied(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    flow = rt.flows.get("incident-investigation").model_copy(
        update={"remediation_tools": frozenset({"drop_database"})}
    )
    ctx = rt.ctx(inc, flow=flow, phase="remediate", actor=human(Role.ADMIN))
    d = await authorize(
        rt,
        rt.request(inc, "drop_database", {"target": "postgres"}, actor=human(Role.ADMIN)),
        ctx,
        in_loop=False,
    )
    assert not d.allowed and d.failed_check.name == "category_permitted"  # type: ignore[union-attr]


async def test_inactive_incident_blocks_everything(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    inc.status = IncidentStatus.RESOLVED
    ctx = rt.ctx(inc)
    d = await authorize(
        rt, rt.request(inc, "get_metrics", {"service": "api-gateway", "metric": "error_rate"}), ctx
    )
    assert d.denial_code == "incident_inactive"


async def test_disabled_tool(rt) -> None:  # type: ignore[no-untyped-def]
    rt.registry.disable("get_logs")
    inc = rt.incident()
    d = await authorize(rt, rt.request(inc, "get_logs", {"service": "api-gateway"}), rt.ctx(inc))
    assert d.denial_code == "tool_disabled"
    rt.registry.enable("get_logs")
    d = await authorize(rt, rt.request(inc, "get_logs", {"service": "api-gateway"}), rt.ctx(inc))
    assert d.allowed


async def test_actor_role_required(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    viewer = human(Role.VIEWER, "viewer")
    ctx = rt.ctx(inc, phase="validate", actor=viewer)
    d = await authorize(
        rt, rt.request(inc, "run_cache_diagnostic", {"component": "redis"}, actor=viewer), ctx
    )
    assert d.denial_code == "tool_not_allowed" and d.failed_check.name == "actor_permitted"  # type: ignore[union-attr]
    d = await authorize(
        rt,
        rt.request(inc, "get_metrics", {"service": "redis", "metric": "connections"}, actor=viewer),
        rt.ctx(inc, actor=viewer),
    )
    assert d.allowed  # read-only is fine for viewers


async def test_risk_ceiling(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    flow = rt.flows.get("incident-investigation")
    flow = flow.model_copy(
        update={
            "phases": tuple(
                p.model_copy(update={"risk_ceiling": RiskLevel.NONE}) if p.name == "validate" else p
                for p in flow.phases
            )
        }
    )
    ctx = rt.ctx(inc, flow=flow, phase="validate")
    d = await authorize(rt, rt.request(inc, "run_cache_diagnostic", {"component": "redis"}), ctx)
    assert d.denial_code == "risk_ceiling_exceeded"


async def test_environment_and_severity_gates(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident(severity=Severity.SEV4)
    defn = rt.registry.get("get_health")
    original = defn.spec
    defn.spec = original.model_copy(
        update={"allowed_environments": frozenset({Environment.PRODUCTION})}
    )
    d = await authorize(rt, rt.request(inc, "get_health", {"service": "redis"}), rt.ctx(inc))
    assert d.denial_code == "tool_not_allowed" and d.failed_check.name == "environment_allows"  # type: ignore[union-attr]
    defn.spec = original.model_copy(update={"min_severity": Severity.SEV2})
    d = await authorize(rt, rt.request(inc, "get_health", {"service": "redis"}), rt.ctx(inc))
    assert d.failed_check.name == "severity_allows"  # type: ignore[union-attr]
    defn.spec = original


async def test_budget_exhaustion(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    d = await authorize(
        rt,
        rt.request(inc, "get_health", {"service": "redis"}),
        rt.ctx(inc),
        budget=ExecutionBudget(max_tool_calls=3),
        usage=BudgetUsage(tool_calls=3),
    )
    assert d.denial_code == "execution_budget_exceeded"
    assert "tool_calls 3 >= 3" in d.reason


async def test_workflow_mutation_requires_matching_approval(rt) -> None:  # type: ignore[no-untyped-def]
    """An approval authorizes one action, on one incident, for a bounded time.

    Binding it to the plan id alone would let a single human decision authorize a different
    action, on a different incident, an arbitrary time later.
    """
    inc = rt.incident()
    plan_id = uuid.uuid4()
    wf = Actor.workflow("wf-1")
    arguments = {"service": "order-service"}
    ctx = rt.ctx(inc, phase="remediate", actor=wf, action_plan_id=plan_id)
    req = rt.request(inc, "restart_service", arguments, actor=wf, action_plan_id=plan_id)

    d = await authorize(rt, req, ctx, in_loop=False)
    assert (
        not d.allowed
        and d.effect is PolicyEffect.REQUIRE_APPROVAL
        and d.denial_code == "approval_required"
    )
    with pytest.raises(ApprovalRequired):
        raise_for(d)

    now = rt.engine.now
    granted = ApprovalRequest(
        incident_id=inc.id,
        action_plan_id=plan_id,
        title="restart_service on order-service",
        summary="s",
        risk=RiskLevel.MEDIUM,
        requested_at=now,
        expires_at=now + timedelta(minutes=10),
        status=ApprovalStatus.APPROVED,
        decided_by="ops",
        decided_at=now,
        context={"tool_name": "restart_service", "arguments": arguments},
    )
    d = await authorize(rt, req, ctx, in_loop=False, approval=granted, now=now)
    assert d.allowed and "granted" in d.checks[10].detail

    async def refused(**changes: object) -> str | None:
        decision = await authorize(
            rt, req, ctx, in_loop=False, approval=granted.model_copy(update=changes), now=now
        )
        return decision.denial_code

    assert await refused(action_plan_id=uuid.uuid4()) == "approval_required"  # another plan
    assert await refused(incident_id=uuid.uuid4()) == "approval_required"  # another incident
    assert await refused(status=ApprovalStatus.PENDING) == "approval_required"  # not decided
    assert await refused(status=ApprovalStatus.REJECTED) == "approval_required"
    assert await refused(decided_at=None) == "approval_required"
    assert await refused(decided_at=now - timedelta(hours=3)) == "approval_required"  # stale
    assert (
        await refused(context={"tool_name": "restart_service", "arguments": {"service": "redis"}})
        == "approval_required"
    )  # approved a different target
    assert (
        await refused(context={"tool_name": "rollback_deployment", "arguments": arguments})
        == "approval_required"
    )  # approved a different tool
    assert await refused(context={}) == "approval_required"  # says nothing about what it approved


async def test_an_approved_plans_rollback_runs_under_the_same_approval(rt) -> None:  # type: ignore[no-untyped-def]
    """Verification can fail long after the decision; the rollback presented with the plan must
    still be executable without asking the operator again."""
    inc = rt.incident()
    plan_id = uuid.uuid4()
    wf = Actor.workflow("wf-1")
    now = rt.engine.now
    action = {"service": "payment-service"}
    rollback = {"service": "payment-service", "to_version": "2.4.0"}
    approval = ApprovalRequest(
        incident_id=inc.id,
        action_plan_id=plan_id,
        title="rollback_deployment on payment-service",
        summary="s",
        risk=RiskLevel.MEDIUM,
        requested_at=now,
        expires_at=now + timedelta(minutes=10),
        status=ApprovalStatus.APPROVED,
        decided_by="ops",
        decided_at=now,
        context={
            "tool_name": "rollback_deployment",
            "arguments": action,
            "rollback": {"tool_name": "rollback_deployment", "arguments": rollback},
        },
    )
    ctx = rt.ctx(inc, phase="remediate", actor=wf, action_plan_id=plan_id)
    for arguments in (action, rollback):
        req = rt.request(inc, "rollback_deployment", arguments, actor=wf, action_plan_id=plan_id)
        d = await authorize(rt, req, ctx, in_loop=False, approval=approval, now=now)
        assert d.allowed, (arguments, d.reason)
    # but not some third action the operator never saw
    req = rt.request(
        inc, "rollback_deployment", {"service": "order-service"}, actor=wf, action_plan_id=plan_id
    )
    d = await authorize(rt, req, ctx, in_loop=False, approval=approval, now=now)
    assert d.denial_code == "approval_required"


async def test_low_risk_mutation_autonomous_in_dev_but_not_prod() -> None:
    dev = build_runtime(warmup=30)
    inc = dev.incident()
    wf = Actor.workflow("wf")
    ctx = dev.ctx(inc, phase="remediate", actor=wf, action_plan_id=uuid.uuid4())
    d = await authorize(
        dev,
        dev.request(
            inc,
            "rotate_connection_pool",
            {"service": "order-service", "target": "redis"},
            actor=wf,
            action_plan_id=ctx.action_plan_id,
        ),
        ctx,
        in_loop=False,
    )
    assert d.allowed and d.matched_policy == "allow-low-risk-mutations-development"
    prod = build_runtime(environment=Environment.PRODUCTION, warmup=30)
    inc = prod.incident()
    ctx = prod.ctx(inc, phase="remediate", actor=wf, action_plan_id=uuid.uuid4())
    d = await authorize(
        prod,
        prod.request(
            inc,
            "rotate_connection_pool",
            {"service": "order-service", "target": "redis"},
            actor=wf,
            action_plan_id=ctx.action_plan_id,
        ),
        ctx,
        in_loop=False,
    )
    assert d.denial_code == "approval_required"


async def test_concurrent_duplicate_detected_via_ledger(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    plan_id = uuid.uuid4()
    wf = Actor.workflow("wf")
    req = rt.request(
        inc,
        "rotate_connection_pool",
        {"service": "order-service", "target": "redis"},
        actor=wf,
        action_plan_id=plan_id,
    )
    key = idempotency_key_for(req, ToolCategory.MUTATING)
    ctx = rt.ctx(inc, phase="remediate", actor=wf, action_plan_id=plan_id)
    first = await authorize(rt, req, ctx, in_loop=False)
    assert first.allowed
    await rt.uow.tool_executions.add(
        ToolExecutionRecord(
            incident_id=inc.id,
            request_id=uuid.uuid4(),
            tool_name=req.tool_name,
            tool_version="1",
            category=ToolCategory.MUTATING,
            arguments=req.arguments,
            idempotency_key=key,
            status=ExecutionStatus.RUNNING,
            authorization=first,
        )
    )
    second = await authorize(rt, req, ctx, in_loop=False)
    assert second.denial_code == "duplicate_execution"


def test_idempotency_keys_scope_reads_per_request_and_mutations_per_plan(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    a = rt.request(inc, "get_metrics", {"service": "redis", "metric": "connections"})
    b = rt.request(inc, "get_metrics", {"service": "redis", "metric": "connections"})
    assert idempotency_key_for(a, ToolCategory.READ_ONLY) != idempotency_key_for(
        b, ToolCategory.READ_ONLY
    )
    plan = uuid.uuid4()
    m1 = rt.request(inc, "restart_service", {"service": "x"}, action_plan_id=plan)
    m2 = rt.request(inc, "restart_service", {"service": "x"}, action_plan_id=plan)
    assert idempotency_key_for(m1, ToolCategory.MUTATING) == idempotency_key_for(
        m2, ToolCategory.MUTATING
    )
    with pytest.raises(ToolNotRegistered):
        rt.registry.get("nope")
    with pytest.raises(PhaseViolation):
        raise_for(
            type(a)  # smoke: raise_for maps codes
            and __import__(
                "aegis.domain.tool", fromlist=["AuthorizationDecision"]
            ).AuthorizationDecision(
                request_id=a.id,
                tool_name="x",
                effect=PolicyEffect.DENY,
                allowed=False,
                checks=(),
                denial_code="phase_violation",
                reason="r",
            )
        )
