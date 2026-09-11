"""In-memory repositories with the same semantics as the Postgres adapters (unique keys, seq)."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Sequence
from datetime import datetime

from aegis.domain.action import ActionExecution, ActionPlan
from aegis.domain.agent import AgentRun, AgentStep
from aegis.domain.approval import ApprovalRequest
from aegis.domain.audit import AuditEvent
from aegis.domain.enums import ApprovalStatus, IncidentStatus, Severity
from aegis.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from aegis.domain.evidence import Evidence, EvidenceRelation
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident, IncidentEvent
from aegis.domain.memory import IncidentMemory, MemoryMatch
from aegis.domain.notification import Notification
from aegis.domain.tool import ToolExecutionRecord


class InMemoryStore:
    """Shared state so several repositories/unit-of-work instances see the same data."""

    def __init__(self) -> None:
        self.incidents: dict[uuid.UUID, Incident] = {}
        self.events: dict[uuid.UUID, list[IncidentEvent]] = {}
        self.evidence: dict[uuid.UUID, Evidence] = {}
        self.relations: list[EvidenceRelation] = []
        self.hypotheses: dict[uuid.UUID, Hypothesis] = {}
        self.plans: dict[uuid.UUID, ActionPlan] = {}
        self.executions: dict[uuid.UUID, ActionExecution] = {}
        self.approvals: dict[uuid.UUID, ApprovalRequest] = {}
        self.audit: list[AuditEvent] = []
        self.runs: dict[uuid.UUID, AgentRun] = {}
        self.steps: dict[uuid.UUID, list[AgentStep]] = {}
        self.tool_execs: dict[uuid.UUID, ToolExecutionRecord] = {}
        self.memories: dict[uuid.UUID, IncidentMemory] = {}
        self.notifications: dict[uuid.UUID, Notification] = {}
        self.incident_seq = 0
        self.lock = asyncio.Lock()


class InMemoryIncidentRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, incident: Incident) -> Incident:
        if incident.id in self.s.incidents:
            raise ConflictError("incident exists")
        if not incident.number:
            self.s.incident_seq += 1
            incident.number = 1000 + self.s.incident_seq
        self.s.incidents[incident.id] = incident.model_copy(deep=True)
        self.s.events.setdefault(incident.id, [])
        return incident

    async def get(self, incident_id: uuid.UUID) -> Incident:
        inc = self.s.incidents.get(incident_id)
        if inc is None:
            raise NotFoundError(f"incident {incident_id} not found")
        return inc.model_copy(deep=True)

    async def find(self, incident_id: uuid.UUID) -> Incident | None:
        inc = self.s.incidents.get(incident_id)
        return inc.model_copy(deep=True) if inc else None

    async def save(self, incident: Incident) -> Incident:
        """Optimistic concurrency: the caller's version must match the stored one; the repository
        bumps it. Same semantics as the Postgres adapter."""
        current = self.s.incidents.get(incident.id)
        if current is None:
            raise NotFoundError("incident not found")
        if current.version != incident.version:
            raise ConcurrencyError(
                f"incident {incident.display_id} was modified concurrently "
                f"(stored v{current.version}, yours v{incident.version})"
            )
        incident.version += 1
        self.s.incidents[incident.id] = incident.model_copy(deep=True)
        return incident

    async def list_incidents(
        self,
        *,
        status: Sequence[IncidentStatus] | None = None,
        severity: Sequence[Severity] | None = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Incident], int]:
        items = list(self.s.incidents.values())
        if status:
            items = [i for i in items if i.status in status]
        if severity:
            items = [i for i in items if i.severity in severity]
        if active_only:
            items = [i for i in items if i.is_active]
        items.sort(key=lambda i: i.detected_at, reverse=True)
        return [i.model_copy(deep=True) for i in items[offset : offset + limit]], len(items)

    async def find_open_by_correlation(
        self, services: Sequence[str], since: datetime
    ) -> Incident | None:
        for inc in sorted(self.s.incidents.values(), key=lambda i: i.detected_at, reverse=True):
            if (
                inc.is_active
                and inc.detected_at >= since
                and set(services) & set(inc.affected_services)
            ):
                return inc.model_copy(deep=True)
        return None

    async def append_event(self, event: IncidentEvent) -> IncidentEvent:
        events = self.s.events.setdefault(event.incident_id, [])
        stored = event.model_copy(update={"seq": len(events) + 1})
        events.append(stored)
        return stored

    async def events(
        self, incident_id: uuid.UUID, after_seq: int = 0, limit: int = 500
    ) -> list[IncidentEvent]:
        return [e for e in self.s.events.get(incident_id, []) if e.seq > after_seq][:limit]

    async def count_by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for inc in self.s.incidents.values():
            out[inc.status.value] = out.get(inc.status.value, 0) + 1
        return out


class InMemoryEvidenceRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, evidence: Evidence) -> Evidence:
        self.s.evidence[evidence.id] = evidence.model_copy(deep=True)
        return evidence

    async def add_many(self, items: Sequence[Evidence]) -> list[Evidence]:
        return [await self.add(e) for e in items]

    async def get(self, evidence_id: uuid.UUID) -> Evidence:
        e = self.s.evidence.get(evidence_id)
        if e is None:
            raise NotFoundError("evidence not found")
        return e.model_copy(deep=True)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[Evidence]:
        return sorted(
            (
                e.model_copy(deep=True)
                for e in self.s.evidence.values()
                if e.incident_id == incident_id
            ),
            key=lambda e: e.created_at,
        )

    async def add_relation(self, relation: EvidenceRelation) -> EvidenceRelation:
        self.s.relations.append(relation)
        return relation

    async def relations_for_incident(self, incident_id: uuid.UUID) -> list[EvidenceRelation]:
        return [r for r in self.s.relations if r.incident_id == incident_id]

    async def exists(self, incident_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        return {
            i for i in ids if i in self.s.evidence and self.s.evidence[i].incident_id == incident_id
        }


class InMemoryHypothesisRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, hypothesis: Hypothesis) -> Hypothesis:
        self.s.hypotheses[hypothesis.id] = hypothesis.model_copy(deep=True)
        return hypothesis

    async def save(self, hypothesis: Hypothesis) -> Hypothesis:
        if hypothesis.id not in self.s.hypotheses:
            raise NotFoundError("hypothesis not found")
        self.s.hypotheses[hypothesis.id] = hypothesis.model_copy(deep=True)
        return hypothesis

    async def get(self, hypothesis_id: uuid.UUID) -> Hypothesis:
        h = self.s.hypotheses.get(hypothesis_id)
        if h is None:
            raise NotFoundError("hypothesis not found")
        return h.model_copy(deep=True)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[Hypothesis]:
        return sorted(
            (
                h.model_copy(deep=True)
                for h in self.s.hypotheses.values()
                if h.incident_id == incident_id
            ),
            key=lambda h: (-h.confidence, h.created_at),
        )


class InMemoryActionPlanRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, plan: ActionPlan) -> ActionPlan:
        self.s.plans[plan.id] = plan.model_copy(deep=True)
        return plan

    async def save(self, plan: ActionPlan) -> ActionPlan:
        if plan.id not in self.s.plans:
            raise NotFoundError("plan not found")
        self.s.plans[plan.id] = plan.model_copy(deep=True)
        return plan

    async def get(self, plan_id: uuid.UUID) -> ActionPlan:
        p = self.s.plans.get(plan_id)
        if p is None:
            raise NotFoundError("plan not found")
        return p.model_copy(deep=True)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[ActionPlan]:
        return sorted(
            (
                p.model_copy(deep=True)
                for p in self.s.plans.values()
                if p.incident_id == incident_id
            ),
            key=lambda p: p.created_at,
        )

    async def add_execution(self, execution: ActionExecution) -> ActionExecution:
        if any(e.idempotency_key == execution.idempotency_key for e in self.s.executions.values()):
            raise ConflictError(f"execution with key {execution.idempotency_key} exists")
        self.s.executions[execution.id] = execution.model_copy(deep=True)
        return execution

    async def save_execution(self, execution: ActionExecution) -> ActionExecution:
        self.s.executions[execution.id] = execution.model_copy(deep=True)
        return execution

    async def find_execution_by_key(self, idempotency_key: str) -> ActionExecution | None:
        for e in self.s.executions.values():
            if e.idempotency_key == idempotency_key:
                return e.model_copy(deep=True)
        return None

    async def executions_for_plan(self, plan_id: uuid.UUID) -> list[ActionExecution]:
        return sorted(
            (
                e.model_copy(deep=True)
                for e in self.s.executions.values()
                if e.action_plan_id == plan_id
            ),
            key=lambda e: e.created_at,
        )


class InMemoryApprovalRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, approval: ApprovalRequest) -> ApprovalRequest:
        self.s.approvals[approval.id] = approval.model_copy(deep=True)
        return approval

    async def save(self, approval: ApprovalRequest) -> ApprovalRequest:
        self.s.approvals[approval.id] = approval.model_copy(deep=True)
        return approval

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        a = self.s.approvals.get(approval_id)
        if a is None:
            raise NotFoundError("approval not found")
        return a.model_copy(deep=True)

    async def list_approvals(
        self,
        *,
        status: ApprovalStatus | None = None,
        incident_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        items = [
            a
            for a in self.s.approvals.values()
            if (status is None or a.status is status)
            and (incident_id is None or a.incident_id == incident_id)
        ]
        items.sort(key=lambda a: a.requested_at, reverse=True)
        return [a.model_copy(deep=True) for a in items[:limit]]


class InMemoryAuditRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def append(self, event: AuditEvent) -> AuditEvent:
        self.s.audit.append(event)
        return event

    async def list_events(
        self, *, incident_id: uuid.UUID | None = None, limit: int = 200, offset: int = 0
    ) -> list[AuditEvent]:
        items = [a for a in self.s.audit if incident_id is None or a.incident_id == incident_id]
        return items[offset : offset + limit]


class InMemoryAgentRunRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, run: AgentRun) -> AgentRun:
        self.s.runs[run.id] = run.model_copy(deep=True)
        self.s.steps.setdefault(run.id, [])
        return run

    async def save(self, run: AgentRun) -> AgentRun:
        self.s.runs[run.id] = run.model_copy(deep=True)
        return run

    async def get(self, run_id: uuid.UUID) -> AgentRun:
        r = self.s.runs.get(run_id)
        if r is None:
            raise NotFoundError("agent run not found")
        return r.model_copy(deep=True)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[AgentRun]:
        return sorted(
            (r.model_copy(deep=True) for r in self.s.runs.values() if r.incident_id == incident_id),
            key=lambda r: r.started_at,
        )

    async def list_recent(self, limit: int = 50) -> list[AgentRun]:
        return sorted(
            (r.model_copy(deep=True) for r in self.s.runs.values()),
            key=lambda r: r.started_at,
            reverse=True,
        )[:limit]

    async def add_step(self, step: AgentStep) -> AgentStep:
        self.s.steps.setdefault(step.agent_run_id, []).append(step)
        return step

    async def steps_for_run(self, run_id: uuid.UUID) -> list[AgentStep]:
        return sorted(self.s.steps.get(run_id, []), key=lambda s: s.seq)

    async def steps_for_incident(self, incident_id: uuid.UUID) -> list[AgentStep]:
        return sorted(
            (s for steps in self.s.steps.values() for s in steps if s.incident_id == incident_id),
            key=lambda s: (s.at, s.seq),
        )


class InMemoryToolExecutionRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        for existing in self.s.tool_execs.values():
            if existing.idempotency_key == record.idempotency_key and existing.id != record.id:
                raise ConflictError(f"tool execution with key {record.idempotency_key} exists")
        self.s.tool_execs[record.id] = record.model_copy(deep=True)
        return record

    async def save(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        self.s.tool_execs[record.id] = record.model_copy(deep=True)
        return record

    async def get(self, execution_id: uuid.UUID) -> ToolExecutionRecord:
        r = self.s.tool_execs.get(execution_id)
        if r is None:
            raise NotFoundError("tool execution not found")
        return r.model_copy(deep=True)

    async def find_by_key(self, idempotency_key: str) -> ToolExecutionRecord | None:
        for r in self.s.tool_execs.values():
            if r.idempotency_key == idempotency_key:
                return r.model_copy(deep=True)
        return None

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[ToolExecutionRecord]:
        return sorted(
            (
                r.model_copy(deep=True)
                for r in self.s.tool_execs.values()
                if r.incident_id == incident_id
            ),
            key=lambda r: r.created_at,
        )

    async def count_for_run(self, agent_run_id: uuid.UUID) -> int:
        return sum(1 for r in self.s.tool_execs.values() if r.agent_run_id == agent_run_id)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryMemoryRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, memory: IncidentMemory) -> IncidentMemory:
        self.s.memories[memory.id] = memory.model_copy(deep=True)
        return memory

    async def get_for_incident(self, incident_id: uuid.UUID) -> IncidentMemory | None:
        for m in self.s.memories.values():
            if m.incident_id == incident_id:
                return m.model_copy(deep=True)
        return None

    async def list_memories(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[IncidentMemory], int]:
        items = sorted(self.s.memories.values(), key=lambda m: m.created_at, reverse=True)
        return [m.model_copy(deep=True) for m in items[offset : offset + limit]], len(items)

    async def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
        exclude_incident_id: uuid.UUID | None = None,
    ) -> list[MemoryMatch]:
        scored = [
            MemoryMatch(memory=m.model_copy(deep=True), similarity=cosine(embedding, m.embedding))
            for m in self.s.memories.values()
            if m.embedding is not None and m.incident_id != exclude_incident_id
        ]
        scored.sort(key=lambda x: -x.similarity)
        return scored[:limit]

    async def search_lexical(
        self,
        terms: Sequence[str],
        services: Sequence[str],
        *,
        limit: int = 5,
        exclude_incident_id: uuid.UUID | None = None,
    ) -> list[MemoryMatch]:
        out: list[MemoryMatch] = []
        term_set = {t.lower() for t in terms}
        for m in self.s.memories.values():
            if m.incident_id == exclude_incident_id:
                continue
            text = m.to_embedding_text().lower()
            hits = sum(1 for t in term_set if t in text)
            svc_hits = len(set(services) & set(m.affected_services))
            score = (hits + svc_hits) / max(1, len(term_set) + len(services))
            if score > 0:
                out.append(
                    MemoryMatch(
                        memory=m.model_copy(deep=True), similarity=score, matched_on="lexical"
                    )
                )
        out.sort(key=lambda x: -x.similarity)
        return out[:limit]


class InMemoryNotificationRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.s = store

    async def add(self, notification: Notification) -> Notification:
        self.s.notifications[notification.id] = notification
        return notification

    async def list_notifications(
        self, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        items = [n for n in self.s.notifications.values() if not unread_only or not n.read]
        items.sort(key=lambda n: n.at, reverse=True)
        return items[:limit]

    async def mark_read(self, notification_id: uuid.UUID) -> None:
        n = self.s.notifications.get(notification_id)
        if n is not None:
            self.s.notifications[notification_id] = n.model_copy(update={"read": True})


class InMemoryUnitOfWork:
    """No real transactions: commit is a no-op, rollback cannot undo. Adequate for tests."""

    def __init__(self, store: InMemoryStore) -> None:
        self.store = store
        self.incidents = InMemoryIncidentRepository(store)
        self.evidence = InMemoryEvidenceRepository(store)
        self.hypotheses = InMemoryHypothesisRepository(store)
        self.action_plans = InMemoryActionPlanRepository(store)
        self.approvals = InMemoryApprovalRepository(store)
        self.audit = InMemoryAuditRepository(store)
        self.agent_runs = InMemoryAgentRunRepository(store)
        self.tool_executions = InMemoryToolExecutionRepository(store)
        self.memories = InMemoryMemoryRepository(store)
        self.notifications = InMemoryNotificationRepository(store)

    async def __aenter__(self) -> InMemoryUnitOfWork:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class InMemoryUnitOfWorkFactory:
    def __init__(self, store: InMemoryStore | None = None) -> None:
        self.store = store or InMemoryStore()

    def __call__(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self.store)
