"""The exactly-once claim must survive the loss of the transaction that was running the tool.

This is the property the headline safety claim rests on: if a worker is killed between touching
the infrastructure and recording the result, the next attempt must find the claim and refuse.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from aegis.domain.approval import ApprovalRequest
from aegis.domain.base import Actor
from aegis.domain.clock import utcnow
from aegis.domain.enums import (
    ApprovalStatus,
    Environment,
    ExecutionStatus,
    IncidentStatus,
    RiskLevel,
    Severity,
    SignalKind,
)
from aegis.domain.flow import BudgetUsage, ExecutionBudget
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.statemachine import transition
from aegis.domain.tool import ToolCallRequest
from aegis.flow.loader import load_flow_dir
from aegis.flow.registry import FlowRegistry
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.policy.engine import PolicyEngine
from aegis.policy.loader import load_policy_dir
from aegis.simulator.engine import SimulationEngine
from aegis.tools.authorizer import ToolAuthorizer
from aegis.tools.builtin import build_default_registry
from aegis.tools.context import ToolContext
from aegis.tools.executor import ToolExecutor

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
ROOT = Path(__file__).resolve().parents[2]
FLOW_VERSION = "1.1.0"  # the shipped incident-investigation pack


async def _incident(uow_factory) -> Incident:  # type: ignore[no-untyped-def]
    sim = SimulationEngine(seed=3)
    sim.warmup(60)
    signal = AnomalySignal(
        service="redis",
        metric="connections",
        kind=SignalKind.SATURATION,
        observed_value=240,
        baseline_value=12,
        deviation_sigma=30,
        detector="t",
        detected_at=utcnow(),
        window_seconds=15,
    )
    incident = Incident(
        title="redis saturation",
        severity=Severity.SEV2,
        signals=[signal],
        affected_services=["redis", "order-service"],
        environment=Environment.DEVELOPMENT,
        flow_name="incident-investigation",
        flow_version=FLOW_VERSION,
    )
    actor = Actor.workflow("wf-claim")
    transition(incident, IncidentStatus.TRIAGING, actor)
    async with uow_factory() as uow:
        await uow.incidents.add(incident)
        await uow.commit()
    return incident


async def test_a_committed_claim_survives_a_lost_transaction_and_refuses_the_retry(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    incident = await _incident(uow_factory)
    sim = SimulationEngine(seed=3)
    sim.warmup(120)
    registry = build_default_registry()
    flows = FlowRegistry(load_flow_dir(ROOT / "flows"))
    policy = PolicyEngine(load_policy_dir(ROOT / "policies"))
    authorizer = ToolAuthorizer(registry, policy, environment=Environment.DEVELOPMENT)
    flow = flows.get("incident-investigation", FLOW_VERSION)
    phase = flow.phase("remediate")
    actor = Actor.workflow("wf-claim")
    plan_id = uuid.uuid4()
    arguments = {"service": "order-service", "target": "redis"}

    def build_request() -> ToolCallRequest:
        return ToolCallRequest(
            incident_id=incident.id,
            tool_name="rotate_connection_pool",
            arguments=arguments,
            requested_by=actor,
            action_plan_id=plan_id,
            phase="remediate",
        )

    def context(inc: Incident) -> ToolContext:
        return ToolContext(
            incident=inc,
            flow=flow,
            phase=phase,
            actor=actor,
            environment=Environment.DEVELOPMENT,
            telemetry=InProcessSimulatorTelemetry(sim),
            infrastructure=InProcessSimulatorGateway(sim),
            known_components=frozenset(sim.component_names()),
            action_plan_id=plan_id,
        )

    # --- the tool runs, then the transaction recording it is lost (worker killed)
    rotations_before = sum(1 for a in sim.action_log if a.get("action") == "rotate_pool")
    async with uow_factory() as uow:
        executor = ToolExecutor(
            registry,
            authorizer,
            ledger=uow.tool_executions,
            evidence=uow.evidence,
            audit=uow.audit,
            claim_factory=uow_factory,
        )
        record = await executor.execute(
            build_request(),
            ctx=context(await uow.incidents.get(incident.id)),
            budget=ExecutionBudget(),
            usage=BudgetUsage(),
            in_agent_loop=False,
        )
        assert record.status is ExecutionStatus.SUCCEEDED, record.error
        await uow.rollback()  # the result write is lost, as on a crash
    assert (
        sum(1 for a in sim.action_log if a.get("action") == "rotate_pool") == rotations_before + 1
    )

    # --- the claim is still there, even though the result write was rolled back
    async with uow_factory() as uow:
        claimed = await uow.tool_executions.find_by_key(record.idempotency_key)
    assert claimed is not None, "the claim must outlive the transaction that ran the tool"
    assert claimed.status is ExecutionStatus.RUNNING

    # --- the retry is refused rather than repeating the infrastructure change
    async with uow_factory() as uow:
        executor = ToolExecutor(
            registry,
            authorizer,
            ledger=uow.tool_executions,
            evidence=uow.evidence,
            audit=uow.audit,
            claim_factory=uow_factory,
        )
        retry = await executor.execute(
            build_request(),
            ctx=context(await uow.incidents.get(incident.id)),
            budget=ExecutionBudget(),
            usage=BudgetUsage(),
            in_agent_loop=False,
        )
        await uow.commit()
    assert retry.status is ExecutionStatus.DENIED
    assert retry.authorization.denial_code == "duplicate_execution"
    assert (
        sum(1 for a in sim.action_log if a.get("action") == "rotate_pool") == rotations_before + 1
    )


async def test_an_approval_for_another_incident_cannot_authorize_this_one(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """An approval is bound to its incident and its approved action, not merely to a plan id."""
    incident = await _incident(uow_factory)
    sim = SimulationEngine(seed=4)
    sim.warmup(120)
    registry = build_default_registry()
    flows = FlowRegistry(load_flow_dir(ROOT / "flows"))
    authorizer = ToolAuthorizer(
        registry,
        PolicyEngine(load_policy_dir(ROOT / "policies")),
        environment=Environment.PRODUCTION,  # forces approval for any mutation
    )
    flow = flows.get("incident-investigation", FLOW_VERSION)
    plan_id = uuid.uuid4()
    actor = Actor.workflow("wf-claim")
    arguments = {"service": "order-service"}
    request = ToolCallRequest(
        incident_id=incident.id,
        tool_name="restart_service",
        arguments=arguments,
        requested_by=actor,
        action_plan_id=plan_id,
        phase="remediate",
    )
    now = utcnow()
    async with uow_factory() as uow:
        stored = await uow.incidents.get(incident.id)
        stored.environment = Environment.PRODUCTION
        await uow.incidents.save(stored)
        await uow.commit()
    ctx_incident = stored

    async def decide(approval: ApprovalRequest | None) -> str | None:
        async with uow_factory() as uow:
            decision = await authorizer.authorize(
                request,
                incident=ctx_incident,
                flow=flow,
                phase=flow.phase("remediate"),
                budget=ExecutionBudget(),
                usage=BudgetUsage(),
                ledger=uow.tool_executions,
                known_components=frozenset(sim.component_names()),
                in_agent_loop=False,
                approval=approval,
                now=now,
            )
        return None if decision.allowed else decision.denial_code

    base = {
        "action_plan_id": plan_id,
        "title": "restart_service on order-service",
        "summary": "s",
        "risk": RiskLevel.MEDIUM,
        "requested_at": now,
        "expires_at": now,
        "status": ApprovalStatus.APPROVED,
        "decided_by": "user:ops",
        "decided_at": now,
        "context": {"tool_name": "restart_service", "arguments": arguments},
    }
    assert await decide(None) == "approval_required"
    # right plan id, wrong incident
    assert (
        await decide(ApprovalRequest(incident_id=uuid.uuid4(), **base))  # type: ignore[arg-type]
        == "approval_required"
    )
    # right incident, but approving different arguments
    mismatched = dict(base)
    mismatched["context"] = {"tool_name": "restart_service", "arguments": {"service": "redis"}}
    assert (
        await decide(ApprovalRequest(incident_id=incident.id, **mismatched))  # type: ignore[arg-type]
        == "approval_required"
    )
    # decided far in the past: a stale approval cannot authorize a fresh execution
    stale = dict(base)
    stale["decided_at"] = now.replace(year=now.year - 1)
    assert (
        await decide(ApprovalRequest(incident_id=incident.id, **stale))  # type: ignore[arg-type]
        == "approval_required"
    )
    # the genuine article
    assert await decide(ApprovalRequest(incident_id=incident.id, **base)) is None  # type: ignore[arg-type]
