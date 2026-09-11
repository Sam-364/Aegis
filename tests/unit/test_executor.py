from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from pydantic import Field

from aegis.domain.approval import ApprovalRequest
from aegis.domain.base import Actor
from aegis.domain.enums import (
    ApprovalStatus,
    EvidenceKind,
    ExecutionStatus,
    RiskLevel,
    ToolCategory,
)
from aegis.domain.errors import InfrastructureError
from aegis.domain.tool import RetryPolicy
from aegis.tools.context import ToolContext
from aegis.tools.definition import ToolArgs, ToolOutput, tool
from tests.helpers import budget, build_runtime


@pytest.fixture
def rt():  # type: ignore[no-untyped-def]
    return build_runtime(warmup=120)


async def run(
    rt,
    inc,
    tool_name,
    args,
    *,
    phase="investigate",
    in_loop=True,
    plan_id=None,
    approval=None,  # type: ignore[no-untyped-def]
    actor=None,
    run_id=None,
):
    actor = actor or (Actor.workflow("wf") if not in_loop else Actor.agent(str(run_id or "run")))
    ctx = rt.ctx(inc, phase=phase, actor=actor, action_plan_id=plan_id, agent_run_id=run_id)
    req = rt.request(inc, tool_name, args, actor=actor, action_plan_id=plan_id, agent_run_id=run_id)
    b, u = budget()
    return await rt.executor.execute(
        req, ctx=ctx, budget=b, usage=u, in_agent_loop=in_loop, approval=approval
    )


async def test_read_tool_produces_evidence_and_audit(rt) -> None:  # type: ignore[no-untyped-def]
    rt.engine.inject("redis-connection-leak")
    rt.engine.advance(100)
    inc = rt.incident()
    rec = await run(rt, inc, "inspect_redis", {"component": "redis"}, run_id=uuid.uuid4())
    assert rec.status is ExecutionStatus.SUCCEEDED, rec.error
    assert rec.duration_ms is not None and rec.evidence_ids
    ev = await rt.uow.evidence.get(rec.evidence_ids[0])
    assert ev.kind is EvidenceKind.DIAGNOSTIC and ev.service == "order-service"
    assert "order-service holds" in ev.summary and ev.strength >= 0.7
    audits = await rt.uow.audit.list_events(incident_id=inc.id)
    assert [a.event_type for a in audits] == ["tool.executed"]
    assert audits[0].data["checks"][-1]["name"] == "budget_available"
    stored = await rt.uow.tool_executions.get(rec.id)
    assert stored.result_summary == rec.result_summary


async def test_every_read_and_diagnostic_tool_runs_against_the_simulator(rt) -> None:  # type: ignore[no-untyped-def]
    rt.engine.inject("bad-deployment")
    rt.engine.advance(40)
    inc = rt.incident(services=["api-gateway", "payment-service"])
    calls = {
        "get_metrics": ({"service": "payment-service", "metric": "error_rate"}, "investigate"),
        "compare_baseline": ({"service": "api-gateway", "metric": "error_rate"}, "investigate"),
        "get_logs": ({"service": "payment-service", "level": "ERROR"}, "investigate"),
        "query_traces": ({"service": "payment-service"}, "investigate"),
        "get_health": ({"service": "payment-service"}, "investigate"),
        "inspect_service": ({"service": "payment-service"}, "investigate"),
        "inspect_dependencies": ({"service": "order-service"}, "investigate"),
        "inspect_deployment": ({"service": "payment-service"}, "investigate"),
        "inspect_database": ({"component": "postgres"}, "investigate"),
        "inspect_redis": ({"component": "redis"}, "investigate"),
        "search_incident_memory": ({"query": "payment errors"}, "investigate"),
        "run_connectivity_test": (
            {"source": "order-service", "target": "payment-service"},
            "validate",
        ),
        "run_database_diagnostic": ({"component": "postgres"}, "validate"),
        "run_cache_diagnostic": ({"component": "redis"}, "validate"),
        "run_process_diagnostic": ({"service": "payment-service"}, "validate"),
        "reproduce_issue": ({"service": "payment-service", "endpoint": "/checkout"}, "validate"),
        "run_load_projection": ({"service": "payment-service", "factor": 2.0}, "validate"),
    }
    for name, (args, phase) in calls.items():
        rec = await run(rt, inc, name, args, phase=phase)
        assert rec.status is ExecutionStatus.SUCCEEDED, f"{name}: {rec.error}"
        assert rec.result_summary
    deploy = next(
        e
        for e in await rt.uow.evidence.list_for_incident(inc.id)
        if e.kind is EvidenceKind.DEPLOYMENT
    )
    assert "2.4.0" in deploy.summary and deploy.strength >= 0.8
    logs = next(
        e for e in await rt.uow.evidence.list_for_incident(inc.id) if e.kind is EvidenceKind.LOG
    )
    assert "CheckoutHandler" in logs.summary


async def test_denied_request_is_recorded_and_has_no_side_effects(rt) -> None:  # type: ignore[no-untyped-def]
    inc = rt.incident()
    before = list(rt.engine.action_log)
    rec = await run(rt, inc, "restart_service", {"service": "order-service"}, phase="remediate")
    assert rec.status is ExecutionStatus.DENIED
    assert rec.authorization.denial_code == "phase_violation"
    assert rt.engine.action_log == before
    audits = await rt.uow.audit.list_events(incident_id=inc.id)
    assert audits[0].event_type == "tool.denied" and audits[0].decision == "phase_violation"


async def test_workflow_mutation_executes_exactly_once(rt) -> None:  # type: ignore[no-untyped-def]
    rt.engine.inject("redis-connection-leak")
    rt.engine.advance(60)
    inc = rt.incident()
    plan_id = uuid.uuid4()
    now = rt.engine.now
    args = {"service": "order-service", "reason": "leak"}
    approval = ApprovalRequest(
        incident_id=inc.id,
        action_plan_id=plan_id,
        title="t",
        summary="s",
        risk=RiskLevel.MEDIUM,
        requested_at=now,
        expires_at=now + timedelta(minutes=10),
        status=ApprovalStatus.APPROVED,
        decided_by="ops",
        decided_at=now,
        context={"tool_name": "restart_service", "arguments": args},
    )
    first = await run(
        rt,
        inc,
        "restart_service",
        args,
        phase="remediate",
        in_loop=False,
        plan_id=plan_id,
        approval=approval,
    )
    assert first.status is ExecutionStatus.SUCCEEDED, first.error
    assert rt.engine.services["order-service"].restart_count == 1
    second = await run(
        rt,
        inc,
        "restart_service",
        args,
        phase="remediate",
        in_loop=False,
        plan_id=plan_id,
        approval=approval,
    )
    assert second.status is ExecutionStatus.SKIPPED_DUPLICATE
    assert second.result == first.result
    assert rt.engine.services["order-service"].restart_count == 1
    audits = [a.event_type for a in await rt.uow.audit.list_events(incident_id=inc.id)]
    assert audits == ["tool.executed", "remediation.skipped_duplicate"]
    # a new attempt (new plan) is a new key and executes
    third = await run(
        rt,
        inc,
        "restart_service",
        args,
        phase="remediate",
        in_loop=False,
        plan_id=uuid.uuid4(),
        approval=approval.model_copy(update={"action_plan_id": plan_id}),
    )
    assert third.status is ExecutionStatus.DENIED  # approval is for the old plan


async def test_low_risk_mutation_runs_without_approval_in_dev(rt) -> None:  # type: ignore[no-untyped-def]
    rt.engine.inject("db-pool-exhaustion")
    rt.engine.advance(60)
    inc = rt.incident()
    rec = await run(
        rt,
        inc,
        "rotate_connection_pool",
        {"service": "user-service", "target": "postgres"},
        phase="remediate",
        in_loop=False,
        plan_id=uuid.uuid4(),
    )
    assert rec.status is ExecutionStatus.SUCCEEDED
    assert rec.result["released_connections"] > 30
    assert not rt.engine.active_faults()


async def test_timeout_and_retry_semantics(rt) -> None:  # type: ignore[no-untyped-def]
    class Args(ToolArgs):
        service: str = Field(default="api-gateway")

    calls = {"n": 0}

    @tool(
        "slow_read",
        description="t",
        category=ToolCategory.READ_ONLY,
        risk=RiskLevel.NONE,
        args=Args,
        timeout_seconds=0.05,
        retry=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.001),
    )
    async def slow_read(ctx: ToolContext, args: Args) -> ToolOutput:
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return ToolOutput(summary="never")

    @tool(
        "flaky_read",
        description="t",
        category=ToolCategory.READ_ONLY,
        risk=RiskLevel.NONE,
        args=Args,
        retry=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.001),
    )
    async def flaky_read(ctx: ToolContext, args: Args) -> ToolOutput:
        calls["n"] += 1
        if calls["n"] < 3:
            raise InfrastructureError("blip")
        return ToolOutput(summary="ok after retries")

    @tool(
        "flaky_mutation",
        description="t",
        category=ToolCategory.MUTATING,
        risk=RiskLevel.LOW,
        args=Args,
    )
    async def flaky_mutation(ctx: ToolContext, args: Args) -> ToolOutput:
        calls["n"] += 1
        raise InfrastructureError("down")

    for t in (slow_read, flaky_read, flaky_mutation):
        rt.registry.register(t)
    inc = rt.incident()
    flow = rt.flows.get("incident-investigation")
    flow = flow.model_copy(
        update={
            "phases": tuple(
                p.model_copy(
                    update={"allowed_tools": p.allowed_tools | {"slow_read", "flaky_read"}}
                )
                if p.name == "investigate"
                else p
                for p in flow.phases
            ),
            "remediation_tools": flow.remediation_tools | {"flaky_mutation"},
        }
    )
    b, u = budget()

    ctx = rt.ctx(inc, flow=flow)
    rec = await rt.executor.execute(rt.request(inc, "slow_read"), ctx=ctx, budget=b, usage=u)
    assert rec.status is ExecutionStatus.TIMED_OUT and rec.attempt == 2 and calls["n"] == 2

    calls["n"] = 0
    rec = await rt.executor.execute(rt.request(inc, "flaky_read"), ctx=ctx, budget=b, usage=u)
    assert rec.status is ExecutionStatus.SUCCEEDED and rec.attempt == 3

    calls["n"] = 0
    wf = Actor.workflow("wf")
    ctx = rt.ctx(inc, flow=flow, phase="remediate", actor=wf, action_plan_id=uuid.uuid4())
    rec = await rt.executor.execute(
        rt.request(inc, "flaky_mutation", actor=wf, action_plan_id=ctx.action_plan_id),
        ctx=ctx,
        budget=b,
        usage=u,
        in_agent_loop=False,
    )
    assert (
        rec.status is ExecutionStatus.FAILED and calls["n"] == 1
    )  # mutations are never auto-retried
