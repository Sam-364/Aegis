"""End-to-end incident lifecycle through Temporal with in-process simulator and in-memory store."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from temporalio.client import WorkflowFailureError

from aegis.application.container import RuntimeContainer, build_container
from aegis.config import Settings
from aegis.detection.engine import DetectionEngine
from aegis.domain.base import Actor
from aegis.domain.enums import ActionPlanStatus, ApprovalStatus, IncidentStatus, Role
from aegis.infrastructure.memory.repositories import InMemoryStore, InMemoryUnitOfWorkFactory
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.infrastructure.temporal.controller import TemporalWorkflowController
from aegis.simulator.engine import SimulationEngine
from aegis.workflows.contracts import IncidentWorkflowInput
from aegis.workflows.incident_workflow import IncidentWorkflow
from aegis.workflows.worker import build_worker

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.timeout(300),
    pytest.mark.asyncio(loop_scope="module"),
]
ROOT = Path(__file__).resolve().parents[2]


class SimClock:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    def now(self) -> datetime:
        return self.engine.now


class SimSleeper:
    """Verification sleeps advance the simulator instead of wall-clock time."""

    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    async def __call__(self, seconds: float) -> None:
        self.engine.advance(seconds)
        await asyncio.sleep(0)


def make_container(
    engine: SimulationEngine, store: InMemoryStore, *, environment: str = "development"
) -> RuntimeContainer:
    settings = Settings(
        llm_provider="disabled",
        environment=environment,
        approval_timeout_seconds=120,
        verification_poll_seconds=5,
        _env_file=None,
    )  # type: ignore[call-arg]
    return build_container(
        settings,
        telemetry=InProcessSimulatorTelemetry(engine),
        infrastructure=InProcessSimulatorGateway(engine),
        uow_factory=InMemoryUnitOfWorkFactory(store),
        clock=SimClock(engine),
        sleeper=SimSleeper(engine),
        root=ROOT,
    )


async def detect(
    engine: SimulationEngine, container: RuntimeContainer, scenario: str, seconds: int = 100
):  # type: ignore[no-untyped-def]
    det = DetectionEngine(
        container.telemetry, container.intake, interval_seconds=5, clock=SimClock(engine)
    )
    await det.bootstrap()
    engine.inject(scenario)
    for _ in range(seconds // 5):
        engine.advance(5)
        await det.cycle()
    incidents = await container.intake.open_incidents()
    assert len(incidents) == 1, [i.title for i in incidents]
    return incidents[0]


async def test_full_lifecycle_with_approval(temporal_env) -> None:  # type: ignore[no-untyped-def]
    engine = SimulationEngine(seed=21)
    engine.warmup(600)
    store = InMemoryStore()
    container = make_container(engine, store)
    controller = TemporalWorkflowController(
        temporal_env.client, task_queue="test-incidents", approval_timeout_seconds=120
    )
    container.workflows = controller
    container.intake.workflows = controller
    container.approvals.workflows = controller
    async with build_worker(temporal_env.client, container, task_queue="test-incidents"):
        incident = await detect(engine, container, "redis-connection-leak")
        assert incident.workflow_id == f"incident-{incident.id}"
        handle = controller.handle(incident.workflow_id)  # type: ignore[arg-type]

        # wait until the workflow asks for approval (restart_service is medium risk in development)
        pending = []
        for _ in range(120):
            pending = await container.approvals.list_pending(incident.id)
            if pending:
                break
            await asyncio.sleep(0.5)
        assert pending, "workflow never requested approval"
        approval = pending[0]
        current = await container.incidents.get(incident.id)
        assert current.status is IncidentStatus.AWAITING_APPROVAL
        plan = (await container.incidents.actions(incident.id))[0]
        assert plan.tool_name in ("restart_service", "rotate_connection_pool")
        assert plan.arguments["service"] == "order-service"
        assert plan.status is ActionPlanStatus.AWAITING_APPROVAL
        status = await handle.query(IncidentWorkflow.status)
        assert status.awaiting_approval_id == approval.id

        # the agent may not approve
        with pytest.raises(Exception, match="agent"):
            await container.approvals.decide(approval.id, approved=True, actor=Actor.agent("x"))
        # a human operator approves → workflow resumes → executes exactly once → verifies → resolves
        await container.approvals.decide(
            approval.id,
            approved=True,
            actor=Actor.human("ops", frozenset({Role.OPERATOR}), "Ops"),
            reason="go",
        )
        result = await asyncio.wait_for(handle.result(), timeout=240)
        assert result.outcome == "resolved", result
        final = await container.incidents.get(incident.id)
        assert final.status is IncidentStatus.RESOLVED
        assert final.resolved_at is not None
        plans = await container.incidents.actions(incident.id)
        assert plans[0].status is ActionPlanStatus.VERIFIED
        assert plans[0].verification_result is not None and plans[0].verification_result.after
        assert engine.services["order-service"].restart_count == 1
        assert not engine.active_faults()
        approvals = await container.approvals.list_all(incident.id)
        assert (
            approvals[0].status is ApprovalStatus.APPROVED and approvals[0].decided_by == "user:ops"
        )
        memory = next(iter(store.memories.values()))
        assert memory.root_cause_service == "order-service" and memory.outcome == "resolved"
        events = [e.type for e in await container.incidents.timeline(incident.id)]
        for expected in [
            "incident.created",
            "flow.selected",
            "agent.run_started",
            "tool.executed",
            "hypothesis.created",
            "remediation.proposed",
            "policy.decided",
            "approval.requested",
            "approval.decided",
            "remediation.started",
            "remediation.completed",
            "verification.started",
            "verification.passed",
            "incident.resolved",
            "memory.stored",
        ]:
            assert expected in events, f"missing {expected}"
        audits = await container.incidents.audit(incident.id)
        assert any(a.event_type == "approval.decided" for a in audits)
        assert any(
            a.event_type == "tool.executed" and a.tool_name == plan.tool_name for a in audits
        )


async def test_rejection_leads_to_replan_then_escalation(temporal_env) -> None:  # type: ignore[no-untyped-def]
    engine = SimulationEngine(seed=22)
    engine.warmup(600)
    store = InMemoryStore()
    container = make_container(engine, store)
    controller = TemporalWorkflowController(
        temporal_env.client, task_queue="test-incidents-2", approval_timeout_seconds=120
    )
    container.workflows = controller
    container.intake.workflows = controller
    container.approvals.workflows = controller
    async with build_worker(temporal_env.client, container, task_queue="test-incidents-2"):
        incident = await detect(engine, container, "bad-deployment", seconds=60)
        handle = controller.handle(incident.workflow_id)  # type: ignore[arg-type]
        ops = Actor.human("ops", frozenset({Role.ADMIN}))
        decided = 0
        for _ in range(240):
            pending = await container.approvals.list_pending(incident.id)
            if pending:
                await container.approvals.decide(
                    pending[0].id, approved=False, actor=ops, reason="not during business hours"
                )
                decided += 1
            desc = await handle.describe()
            if desc.status is not None and desc.status.name != "RUNNING":
                break
            await asyncio.sleep(0.5)
        result = await asyncio.wait_for(handle.result(), timeout=120)
        assert decided == 2, decided  # first rejection → replan; second rejection → escalate
        assert result.outcome == "escalated"
        final = await container.incidents.get(incident.id)
        assert final.status is IncidentStatus.ESCALATED
        assert engine.active_faults(), "nothing must have been executed"
        assert engine.services["payment-service"].version == "2.4.0"
        plans = await container.incidents.actions(incident.id)
        assert len(plans) == 2 and all(p.status is ActionPlanStatus.REJECTED for p in plans)


async def test_transient_spike_resolves_without_action(temporal_env) -> None:  # type: ignore[no-untyped-def]
    engine = SimulationEngine(seed=23)
    engine.warmup(600)
    store = InMemoryStore()
    container = make_container(engine, store)
    controller = TemporalWorkflowController(temporal_env.client, task_queue="test-incidents-3")
    container.workflows = controller
    container.intake.workflows = controller
    async with build_worker(temporal_env.client, container, task_queue="test-incidents-3"):
        incident = await detect(engine, container, "transient-spike", seconds=30)
        engine.advance(60)  # spike ends
        handle = controller.handle(incident.workflow_id)  # type: ignore[arg-type]
        result = await asyncio.wait_for(handle.result(), timeout=240)
        assert result.outcome == "resolved", result
        final = await container.incidents.get(incident.id)
        assert final.status is IncidentStatus.RESOLVED
        assert not await container.incidents.actions(incident.id)
        events = [e.type for e in await container.incidents.timeline(incident.id)]
        assert "verification.passed" in events and "remediation.proposed" not in events
        memory = next(iter(store.memories.values()))
        assert memory.outcome == "false_positive"


async def test_cancel_signal_closes_incident(temporal_env) -> None:  # type: ignore[no-untyped-def]
    engine = SimulationEngine(seed=24)
    engine.warmup(600)
    store = InMemoryStore()
    container = make_container(engine, store)
    controller = TemporalWorkflowController(temporal_env.client, task_queue="test-incidents-4")
    container.workflows = controller
    container.intake.workflows = controller
    container.incidents.workflows = controller
    async with build_worker(temporal_env.client, container, task_queue="test-incidents-4"):
        incident = await detect(engine, container, "cascading-dependency", seconds=40)
        ops = Actor.human("ops", frozenset({Role.ADMIN}))
        # close from the API while the agent is investigating
        for _ in range(60):
            current = await container.incidents.get(incident.id)
            if current.status in (IncidentStatus.INVESTIGATING, IncidentStatus.HYPOTHESIS_FORMED):
                break
            await asyncio.sleep(0.25)
        await container.incidents.change_status(
            incident.id, IncidentStatus.CLOSED, ops, "handled manually"
        )
        handle = controller.handle(incident.workflow_id)  # type: ignore[arg-type]
        try:
            result = await asyncio.wait_for(handle.result(), timeout=120)
            assert result.outcome == "closed"
        except (
            WorkflowFailureError
        ):  # pragma: no cover - the workflow may still be finishing an activity
            pass
        final = await container.incidents.get(incident.id)
        assert final.status is IncidentStatus.CLOSED
        assert engine.active_faults()  # no remediation executed
        _ = IncidentWorkflowInput
        _ = uuid
