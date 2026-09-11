from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from aegis.domain.action import ActionExecution, ActionPlan, VerificationCondition, VerificationSpec
from aegis.domain.agent import AgentRun, AgentStep
from aegis.domain.approval import ApprovalRequest
from aegis.domain.audit import AuditEvent
from aegis.domain.base import Actor
from aegis.domain.clock import utcnow
from aegis.domain.enums import (
    AgentStepKind,
    ApprovalStatus,
    EvidenceKind,
    ExecutionStatus,
    GraphNodeKind,
    HypothesisCategory,
    IncidentStatus,
    NotificationKind,
    PolicyEffect,
    RelationKind,
    RiskLevel,
    Severity,
    SignalKind,
    ToolCategory,
)
from aegis.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from aegis.domain.evidence import Evidence, EvidenceRelation
from aegis.domain.flow import ExecutionBudget
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import AnomalySignal, Incident, IncidentEvent
from aegis.domain.memory import IncidentMemory
from aegis.domain.notification import Notification
from aegis.domain.statemachine import transition
from aegis.domain.tool import AuthorizationDecision, ToolExecutionRecord

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def make_incident(**over: object) -> Incident:
    sig = AnomalySignal(
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
    base: dict[str, object] = {
        "title": "API latency regression",
        "severity": Severity.SEV2,
        "signals": [sig],
        "affected_services": ["api-gateway", "redis"],
    }
    base.update(over)
    return Incident(**base)  # type: ignore[arg-type]


async def test_incident_roundtrip_numbering_events_and_optimistic_locking(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inc = make_incident()
    async with uow_factory() as uow:
        stored = await uow.incidents.add(inc)
        assert stored.number >= 1042
        ev = await uow.incidents.append_event(
            IncidentEvent(
                incident_id=inc.id, type="incident.created", actor=Actor.detector(), title="created"
            )
        )
        ev2 = await uow.incidents.append_event(
            IncidentEvent(
                incident_id=inc.id, type="flow.selected", actor=Actor.detector(), title="flow"
            )
        )
        assert (ev.seq, ev2.seq) == (1, 2)
        await uow.commit()
    async with uow_factory() as uow:
        loaded = await uow.incidents.get(inc.id)
        assert loaded.title == inc.title and loaded.number == stored.number and loaded.version == 1
        assert loaded.signals[0].service == "redis"
        events = await uow.incidents.events(inc.id)
        assert [e.seq for e in events] == [1, 2] and events[1].type == "flow.selected"
        assert await uow.incidents.events(inc.id, after_seq=1) == [events[1]]
        transition(loaded, IncidentStatus.TRIAGING, Actor.workflow("wf"))
        await uow.incidents.save(loaded)
        assert loaded.version == 2
        await uow.commit()
    # a stale copy cannot overwrite
    async with uow_factory() as uow:
        stale = make_incident()
        stale.id = inc.id
        stale.version = 1
        with pytest.raises(ConcurrencyError):
            await uow.incidents.save(stale)
    async with uow_factory() as uow:
        items, total = await uow.incidents.list_incidents(status=[IncidentStatus.TRIAGING])
        assert total == 1 and items[0].id == inc.id
        assert (await uow.incidents.count_by_status())["triaging"] == 1
        found = await uow.incidents.find_open_by_correlation(
            ["redis"], utcnow() - timedelta(hours=1)
        )
        assert found is not None and found.id == inc.id
        assert (
            await uow.incidents.find_open_by_correlation(
                ["postgres"], utcnow() - timedelta(hours=1)
            )
            is None
        )
        with pytest.raises(NotFoundError):
            await uow.incidents.get(uuid.uuid4())
        with pytest.raises(ConflictError):
            await uow.incidents.add(make_incident(id=inc.id))


async def test_evidence_hypotheses_plans_approvals_and_idempotency(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inc = make_incident()
    async with uow_factory() as uow:
        await uow.incidents.add(inc)
        e1 = Evidence(
            incident_id=inc.id,
            kind=EvidenceKind.DIAGNOSTIC,
            source="inspect_redis",
            service="order-service",
            title="redis analysis",
            summary="order-service holds 94%",
            strength=0.9,
            data={"component": "redis", "clients_by_service": {"order-service": 240}},
        )
        e2 = Evidence(
            incident_id=inc.id,
            kind=EvidenceKind.METRIC,
            source="get_metrics",
            service="api-gateway",
            title="gw",
            summary="latency up",
            strength=0.7,
        )
        await uow.evidence.add_many([e1, e2])
        hyp = Hypothesis(
            incident_id=inc.id,
            statement="order-service leaks redis connections",
            category=HypothesisCategory.RESOURCE_EXHAUSTION,
            suspected_root_cause_service="order-service",
            supporting_evidence_ids=[e1.id, e2.id],
            confidence=0.8,
        )
        await uow.hypotheses.add(hyp)
        rel = EvidenceRelation(
            incident_id=inc.id,
            from_id=str(e1.id),
            from_kind=GraphNodeKind.EVIDENCE,
            to_id=str(hyp.id),
            to_kind=GraphNodeKind.HYPOTHESIS,
            kind=RelationKind.SUPPORTS,
        )
        await uow.evidence.add_relation(rel)
        await uow.evidence.add_relation(
            rel.model_copy(update={"id": uuid.uuid4()})
        )  # duplicate ignored
        plan = ActionPlan(
            incident_id=inc.id,
            hypothesis_id=hyp.id,
            tool_name="restart_service",
            arguments={"service": "order-service"},
            reason="leak",
            expected_effect="recover",
            verification=VerificationSpec(
                conditions=(
                    VerificationCondition(
                        metric="latency_p95_ms", service="api-gateway", max_ratio_to_baseline=1.5
                    ),
                )
            ),
        )
        await uow.action_plans.add(plan)
        approval = ApprovalRequest(
            incident_id=inc.id,
            action_plan_id=plan.id,
            title="restart",
            summary="s",
            risk=RiskLevel.MEDIUM,
            requested_at=utcnow(),
            expires_at=utcnow() + timedelta(minutes=15),
        )
        await uow.approvals.add(approval)
        await uow.commit()
    async with uow_factory() as uow:
        items = await uow.evidence.list_for_incident(inc.id)
        assert [e.title for e in items] == ["redis analysis", "gw"]
        assert await uow.evidence.exists(inc.id, [e1.id, uuid.uuid4()]) == {e1.id}
        assert len(await uow.evidence.relations_for_incident(inc.id)) == 1
        h = (await uow.hypotheses.list_for_incident(inc.id))[0]
        assert h.statement == hyp.statement and h.supporting_evidence_ids == [e1.id, e2.id]
        h.confidence = 0.95
        await uow.hypotheses.save(h)
        p = await uow.action_plans.get(plan.id)
        assert (
            p.verification.conditions[0].max_ratio_to_baseline == 1.5
            and p.idempotency_key == plan.idempotency_key
        )
        ex = ActionExecution(
            action_plan_id=plan.id,
            incident_id=inc.id,
            idempotency_key="k1",
            status=ExecutionStatus.SUCCEEDED,
        )
        await uow.action_plans.add_execution(ex)
        with pytest.raises(ConflictError):
            await uow.action_plans.add_execution(ex.model_copy(update={"id": uuid.uuid4()}))
        assert (await uow.action_plans.find_execution_by_key("k1")) is not None
        pend = await uow.approvals.list_approvals(status=ApprovalStatus.PENDING, incident_id=inc.id)
        assert len(pend) == 1
        pend[0].status = ApprovalStatus.APPROVED
        await uow.approvals.save(pend[0])
        await uow.commit()
    async with uow_factory() as uow:
        assert (await uow.hypotheses.get(hyp.id)).confidence == 0.95
        assert (await uow.approvals.get(approval.id)).status is ApprovalStatus.APPROVED


async def test_tool_execution_ledger_is_exactly_once(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inc = make_incident()
    decision = AuthorizationDecision(
        request_id=uuid.uuid4(),
        tool_name="restart_service",
        effect=PolicyEffect.ALLOW,
        allowed=True,
        checks=(),
    )
    rec = ToolExecutionRecord(
        incident_id=inc.id,
        request_id=uuid.uuid4(),
        tool_name="restart_service",
        tool_version="1",
        category=ToolCategory.MUTATING,
        arguments={"service": "x"},
        idempotency_key="same-key",
        status=ExecutionStatus.RUNNING,
        authorization=decision,
    )
    async with uow_factory() as uow:
        await uow.incidents.add(inc)
        await uow.tool_executions.add(rec)
        with pytest.raises(ConflictError):
            await uow.tool_executions.add(
                rec.model_copy(update={"id": uuid.uuid4(), "request_id": uuid.uuid4()})
            )
        rec.status = ExecutionStatus.SUCCEEDED
        await uow.tool_executions.save(rec)
        await uow.commit()
    async with uow_factory() as uow:
        found = await uow.tool_executions.find_by_key("same-key")
        assert found is not None and found.status is ExecutionStatus.SUCCEEDED
        assert len(await uow.tool_executions.list_for_incident(inc.id)) == 1


async def test_agent_runs_steps_audit_and_notifications(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inc = make_incident()
    run = AgentRun(
        incident_id=inc.id,
        flow_name="f",
        flow_version="1",
        phase="triage",
        budget=ExecutionBudget(),
    )
    async with uow_factory() as uow:
        await uow.incidents.add(inc)
        await uow.agent_runs.add(run)
        for i in range(3):
            await uow.agent_runs.add_step(
                AgentStep(
                    agent_run_id=run.id,
                    incident_id=inc.id,
                    seq=i + 1,
                    phase="triage",
                    node="propose",
                    kind=AgentStepKind.PROPOSAL,
                    title=f"s{i}",
                )
            )
        with pytest.raises(ConflictError):
            await uow.agent_runs.add_step(
                AgentStep(
                    agent_run_id=run.id,
                    incident_id=inc.id,
                    seq=1,
                    phase="triage",
                    node="propose",
                    kind=AgentStepKind.PROPOSAL,
                    title="dup",
                )
            )
        await uow.audit.append(
            AuditEvent(
                event_type="tool.executed",
                actor=Actor.agent("r"),
                incident_id=inc.id,
                tool_name="get_metrics",
                decision="executed",
            )
        )
        await uow.notifications.add(
            Notification(kind=NotificationKind.INCIDENT_DETECTED, incident_id=inc.id, title="n")
        )
        await uow.commit()
    async with uow_factory() as uow:
        steps = await uow.agent_runs.steps_for_run(run.id)
        assert [s.seq for s in steps] == [1, 2, 3]
        assert (await uow.agent_runs.list_recent(5))[0].id == run.id
        audits = await uow.audit.list_events(incident_id=inc.id)
        assert audits and audits[-1].event_type == "tool.executed"
        notes = await uow.notifications.list_notifications(unread_only=True)
        assert notes and notes[0].incident_id == inc.id
        await uow.notifications.mark_read(notes[0].id)
        await uow.commit()
    async with uow_factory() as uow:
        assert not [
            n
            for n in await uow.notifications.list_notifications(unread_only=True)
            if n.incident_id == inc.id
        ]


async def test_audit_events_are_immutable_at_the_database(pg_engine, uow_factory) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    async with uow_factory() as uow:
        ev = AuditEvent(event_type="x", actor=Actor.system())
        await uow.audit.append(ev)
        await uow.commit()
    async with pg_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": str(ev.id)})
    async with pg_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                text("UPDATE audit_events SET event_type = 'y' WHERE id = :id"), {"id": str(ev.id)}
            )
    # A row-level trigger does not fire for TRUNCATE: the whole trail would go in one statement.
    async with pg_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(text("TRUNCATE audit_events"))
    async with uow_factory() as uow:
        assert ev.id in {e.id for e in await uow.audit.list_events(limit=1000)}


async def test_memory_vector_and_lexical_search(uow_factory) -> None:  # type: ignore[no-untyped-def]
    def vec(seed: float) -> list[float]:
        v = [0.0] * 1536
        v[int(seed) % 1536] = 1.0
        v[(int(seed) + 1) % 1536] = 0.5
        return v

    a = IncidentMemory(
        incident_id=uuid.uuid4(),
        title="Redis leak",
        severity=Severity.SEV2,
        symptoms=["redis connections up"],
        affected_services=["api-gateway", "redis"],
        root_cause="order-service leaked redis connections",
        root_cause_service="order-service",
        resolution="restart order-service",
        embedding=vec(10),
    )
    a.embedding_text = a.to_embedding_text()
    b = IncidentMemory(
        incident_id=uuid.uuid4(),
        title="Payment regression",
        severity=Severity.SEV2,
        symptoms=["payment 5xx"],
        affected_services=["payment-service"],
        root_cause="bad deploy 2.4.0",
        root_cause_service="payment-service",
        resolution="rollback",
        embedding=vec(500),
    )
    b.embedding_text = b.to_embedding_text()
    async with uow_factory() as uow:
        await uow.memories.add(a)
        await uow.memories.add(b)
        with pytest.raises(ConflictError):
            await uow.memories.add(a.model_copy(update={"id": uuid.uuid4()}))
        await uow.commit()
    async with uow_factory() as uow:
        matches = await uow.memories.search(vec(10), limit=2)
        assert matches[0].memory.incident_id == a.incident_id and matches[0].similarity > 0.99
        assert matches[1].similarity < 0.2
        excluded = await uow.memories.search(vec(10), limit=2, exclude_incident_id=a.incident_id)
        assert all(m.memory.incident_id != a.incident_id for m in excluded)
        lex = await uow.memories.search_lexical(["rollback", "payment"], ["payment-service"])
        assert lex and lex[0].memory.incident_id == b.incident_id
        stored = await uow.memories.get_for_incident(a.incident_id)
        assert stored is not None and stored.embedding is not None and len(stored.embedding) == 1536
        items, total = await uow.memories.list_memories()
        assert total == 2 and all(m.embedding is None for m in items)  # list omits vectors
