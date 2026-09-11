"""Postgres repositories (document + projections)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
from aegis.infrastructure.postgres.models import (
    ActionExecutionRow,
    ActionPlanRow,
    AgentRunRow,
    AgentStepRow,
    ApprovalRow,
    AuditEventRow,
    EvidenceRelationRow,
    EvidenceRow,
    HypothesisRow,
    IncidentEventRow,
    IncidentMemoryRow,
    IncidentRow,
    NotificationRow,
    ToolExecutionRow,
)

ACTIVE_STATUSES = [s.value for s in IncidentStatus if s.is_active]


def _doc(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _load[M: BaseModel](cls: type[M], document: dict[str, Any]) -> M:
    return cls.model_validate(document)


def _tsquery_words(values: Sequence[str]) -> list[str]:
    """Reduce free text to distinct alphanumeric words safe to interpolate into a tsquery."""
    words: list[str] = []
    for value in values:
        for raw in re.split(r"[^0-9A-Za-z]+", value.lower()):
            if len(raw) > 2 and raw not in words:
                words.append(raw)
    return words[:24]


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class _Repo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def _one(self, stmt: Select[Any], what: str) -> Any:
        row = (await self.s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"{what} not found")
        return row


class PostgresIncidentRepository(_Repo):
    async def add(self, incident: Incident) -> Incident:
        doc = _doc(incident)
        stmt = (
            insert(IncidentRow)
            .values(
                id=incident.id,
                tenant_id=incident.tenant_id,
                status=incident.status.value,
                severity=incident.severity.value,
                environment=incident.environment.value,
                flow_name=incident.flow_name,
                flow_version=incident.flow_version,
                correlation_key=incident.correlation_key,
                workflow_id=incident.workflow_id,
                detected_at=incident.detected_at,
                resolved_at=incident.resolved_at,
                closed_at=incident.closed_at,
                version=incident.version,
                created_at=incident.created_at,
                updated_at=incident.updated_at,
                document=doc,
                **({"number": incident.number} if incident.number else {}),
            )
            .returning(IncidentRow.number)
        )
        try:
            number = (await self.s.execute(stmt)).scalar_one()
        except IntegrityError as exc:
            raise ConflictError(f"incident {incident.id} already exists") from exc
        incident.number = int(number)
        await self.s.execute(
            update(IncidentRow).where(IncidentRow.id == incident.id).values(document=_doc(incident))
        )
        return incident

    async def get(self, incident_id: uuid.UUID) -> Incident:
        row = await self._one(select(IncidentRow).where(IncidentRow.id == incident_id), "incident")
        return self._to_domain(row)

    async def find(self, incident_id: uuid.UUID) -> Incident | None:
        row = (
            await self.s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: IncidentRow) -> Incident:
        doc = dict(row.document)
        doc["number"] = row.number
        doc["version"] = row.version
        return _load(Incident, doc)

    async def save(self, incident: Incident) -> Incident:
        expected = incident.version
        incident.version = expected + 1
        result = await self.s.execute(
            update(IncidentRow)
            .where(IncidentRow.id == incident.id, IncidentRow.version == expected)
            .values(
                status=incident.status.value,
                severity=incident.severity.value,
                flow_name=incident.flow_name,
                flow_version=incident.flow_version,
                correlation_key=incident.correlation_key,
                workflow_id=incident.workflow_id,
                resolved_at=incident.resolved_at,
                closed_at=incident.closed_at,
                version=incident.version,
                updated_at=incident.updated_at,
                document=_doc(incident),
            )
        )
        if _rowcount(result) == 0:
            incident.version = expected
            exists = (
                await self.s.execute(
                    select(IncidentRow.version).where(IncidentRow.id == incident.id)
                )
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError(f"incident {incident.id} not found")
            raise ConcurrencyError(
                f"incident {incident.display_id} was modified concurrently "
                f"(stored v{exists}, yours v{expected})"
            )
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
        stmt = select(IncidentRow)
        count_stmt = select(func.count()).select_from(IncidentRow)
        if status:
            stmt = stmt.where(IncidentRow.status.in_([s.value for s in status]))
            count_stmt = count_stmt.where(IncidentRow.status.in_([s.value for s in status]))
        if severity:
            stmt = stmt.where(IncidentRow.severity.in_([s.value for s in severity]))
            count_stmt = count_stmt.where(IncidentRow.severity.in_([s.value for s in severity]))
        if active_only:
            stmt = stmt.where(IncidentRow.status.in_(ACTIVE_STATUSES))
            count_stmt = count_stmt.where(IncidentRow.status.in_(ACTIVE_STATUSES))
        rows = (
            (
                await self.s.execute(
                    stmt.order_by(IncidentRow.detected_at.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = (await self.s.execute(count_stmt)).scalar_one()
        return [self._to_domain(r) for r in rows], int(total)

    async def find_open_by_correlation(
        self, services: Sequence[str], since: datetime
    ) -> Incident | None:
        stmt = (
            select(IncidentRow)
            .where(
                IncidentRow.status.in_(ACTIVE_STATUSES),
                IncidentRow.detected_at >= since,
                text("document->'affected_services' ?| :services").bindparams(
                    services=list(services)
                ),
            )
            .order_by(IncidentRow.detected_at.desc())
            .limit(1)
        )
        row = (await self.s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def append_event(self, event: IncidentEvent) -> IncidentEvent:
        for _ in range(5):
            next_seq = (
                await self.s.execute(
                    select(func.coalesce(func.max(IncidentEventRow.seq), 0) + 1).where(
                        IncidentEventRow.incident_id == event.incident_id
                    )
                )
            ).scalar_one()
            stored = event.model_copy(update={"seq": int(next_seq)})
            try:
                async with self.s.begin_nested():
                    await self.s.execute(
                        insert(IncidentEventRow).values(
                            id=stored.id,
                            incident_id=stored.incident_id,
                            seq=stored.seq,
                            type=stored.type,
                            at=stored.at,
                            document=_doc(stored),
                        )
                    )
                return stored
            except IntegrityError:
                continue
        raise ConflictError("could not allocate an event sequence number")

    async def events(
        self, incident_id: uuid.UUID, after_seq: int = 0, limit: int = 500
    ) -> list[IncidentEvent]:
        rows = (
            (
                await self.s.execute(
                    select(IncidentEventRow)
                    .where(
                        IncidentEventRow.incident_id == incident_id,
                        IncidentEventRow.seq > after_seq,
                    )
                    .order_by(IncidentEventRow.seq)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_load(IncidentEvent, {**r.document, "seq": r.seq}) for r in rows]

    async def count_by_status(self) -> dict[str, int]:
        rows = (
            await self.s.execute(
                select(IncidentRow.status, func.count()).group_by(IncidentRow.status)
            )
        ).all()
        return {status: int(count) for status, count in rows}


class PostgresEvidenceRepository(_Repo):
    async def add(self, evidence: Evidence) -> Evidence:
        await self.s.execute(
            insert(EvidenceRow).values(
                id=evidence.id,
                incident_id=evidence.incident_id,
                kind=evidence.kind.value,
                source=evidence.source,
                service=evidence.service,
                strength=evidence.strength,
                observed_at=evidence.observed_at,
                created_at=evidence.created_at,
                document=_doc(evidence),
            )
        )
        return evidence

    async def add_many(self, items: Sequence[Evidence]) -> list[Evidence]:
        if not items:
            return []
        await self.s.execute(
            insert(EvidenceRow),
            [
                {
                    "id": e.id,
                    "incident_id": e.incident_id,
                    "kind": e.kind.value,
                    "source": e.source,
                    "service": e.service,
                    "strength": e.strength,
                    "observed_at": e.observed_at,
                    "created_at": e.created_at,
                    "document": _doc(e),
                }
                for e in items
            ],
        )
        return list(items)

    async def get(self, evidence_id: uuid.UUID) -> Evidence:
        row = await self._one(select(EvidenceRow).where(EvidenceRow.id == evidence_id), "evidence")
        return _load(Evidence, row.document)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[Evidence]:
        rows = (
            (
                await self.s.execute(
                    select(EvidenceRow)
                    .where(EvidenceRow.incident_id == incident_id)
                    .order_by(EvidenceRow.created_at, EvidenceRow.id)
                )
            )
            .scalars()
            .all()
        )
        return [_load(Evidence, r.document) for r in rows]

    async def add_relation(self, relation: EvidenceRelation) -> EvidenceRelation:
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        stmt = (
            pg_insert(EvidenceRelationRow)
            .values(
                id=relation.id,
                incident_id=relation.incident_id,
                from_id=relation.from_id,
                to_id=relation.to_id,
                kind=relation.kind.value,
                document=_doc(relation),
            )
            .on_conflict_do_nothing()
        )
        await self.s.execute(stmt)
        return relation

    async def relations_for_incident(self, incident_id: uuid.UUID) -> list[EvidenceRelation]:
        rows = (
            (
                await self.s.execute(
                    select(EvidenceRelationRow).where(
                        EvidenceRelationRow.incident_id == incident_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_load(EvidenceRelation, r.document) for r in rows]

    async def exists(self, incident_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not ids:
            return set()
        rows = (
            (
                await self.s.execute(
                    select(EvidenceRow.id).where(
                        EvidenceRow.incident_id == incident_id, EvidenceRow.id.in_(list(ids))
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)


class PostgresHypothesisRepository(_Repo):
    async def add(self, hypothesis: Hypothesis) -> Hypothesis:
        await self.s.execute(
            insert(HypothesisRow).values(
                id=hypothesis.id,
                incident_id=hypothesis.incident_id,
                status=hypothesis.status.value,
                confidence=hypothesis.confidence,
                root_cause_service=hypothesis.suspected_root_cause_service,
                created_at=hypothesis.created_at,
                document=_doc(hypothesis),
            )
        )
        return hypothesis

    async def save(self, hypothesis: Hypothesis) -> Hypothesis:
        result = await self.s.execute(
            update(HypothesisRow)
            .where(HypothesisRow.id == hypothesis.id)
            .values(
                status=hypothesis.status.value,
                confidence=hypothesis.confidence,
                root_cause_service=hypothesis.suspected_root_cause_service,
                document=_doc(hypothesis),
            )
        )
        if _rowcount(result) == 0:
            raise NotFoundError("hypothesis not found")
        return hypothesis

    async def get(self, hypothesis_id: uuid.UUID) -> Hypothesis:
        row = await self._one(
            select(HypothesisRow).where(HypothesisRow.id == hypothesis_id), "hypothesis"
        )
        return _load(Hypothesis, row.document)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[Hypothesis]:
        rows = (
            (
                await self.s.execute(
                    select(HypothesisRow)
                    .where(HypothesisRow.incident_id == incident_id)
                    .order_by(HypothesisRow.confidence.desc(), HypothesisRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [_load(Hypothesis, r.document) for r in rows]


class PostgresActionPlanRepository(_Repo):
    async def add(self, plan: ActionPlan) -> ActionPlan:
        await self.s.execute(
            insert(ActionPlanRow).values(
                id=plan.id,
                incident_id=plan.incident_id,
                hypothesis_id=plan.hypothesis_id,
                tool_name=plan.tool_name,
                status=plan.status.value,
                idempotency_key=plan.idempotency_key,
                created_at=plan.created_at,
                document=_doc(plan),
            )
        )
        return plan

    async def save(self, plan: ActionPlan) -> ActionPlan:
        result = await self.s.execute(
            update(ActionPlanRow)
            .where(ActionPlanRow.id == plan.id)
            .values(status=plan.status.value, document=_doc(plan))
        )
        if _rowcount(result) == 0:
            raise NotFoundError("action plan not found")
        return plan

    async def get(self, plan_id: uuid.UUID) -> ActionPlan:
        row = await self._one(
            select(ActionPlanRow).where(ActionPlanRow.id == plan_id), "action plan"
        )
        return _load(ActionPlan, row.document)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[ActionPlan]:
        rows = (
            (
                await self.s.execute(
                    select(ActionPlanRow)
                    .where(ActionPlanRow.incident_id == incident_id)
                    .order_by(ActionPlanRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [_load(ActionPlan, r.document) for r in rows]

    async def add_execution(self, execution: ActionExecution) -> ActionExecution:
        try:
            async with self.s.begin_nested():
                await self.s.execute(
                    insert(ActionExecutionRow).values(
                        id=execution.id,
                        action_plan_id=execution.action_plan_id,
                        incident_id=execution.incident_id,
                        idempotency_key=execution.idempotency_key,
                        status=execution.status.value,
                        created_at=execution.created_at,
                        document=_doc(execution),
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(f"execution with key {execution.idempotency_key} exists") from exc
        return execution

    async def save_execution(self, execution: ActionExecution) -> ActionExecution:
        await self.s.execute(
            update(ActionExecutionRow)
            .where(ActionExecutionRow.id == execution.id)
            .values(status=execution.status.value, document=_doc(execution))
        )
        return execution

    async def find_execution_by_key(self, idempotency_key: str) -> ActionExecution | None:
        row = (
            await self.s.execute(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        return _load(ActionExecution, row.document) if row else None

    async def executions_for_plan(self, plan_id: uuid.UUID) -> list[ActionExecution]:
        rows = (
            (
                await self.s.execute(
                    select(ActionExecutionRow)
                    .where(ActionExecutionRow.action_plan_id == plan_id)
                    .order_by(ActionExecutionRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [_load(ActionExecution, r.document) for r in rows]


class PostgresApprovalRepository(_Repo):
    async def add(self, approval: ApprovalRequest) -> ApprovalRequest:
        await self.s.execute(
            insert(ApprovalRow).values(
                id=approval.id,
                incident_id=approval.incident_id,
                action_plan_id=approval.action_plan_id,
                status=approval.status.value,
                requested_at=approval.requested_at,
                expires_at=approval.expires_at,
                document=_doc(approval),
            )
        )
        return approval

    async def save(self, approval: ApprovalRequest) -> ApprovalRequest:
        result = await self.s.execute(
            update(ApprovalRow)
            .where(ApprovalRow.id == approval.id)
            .values(status=approval.status.value, document=_doc(approval))
        )
        if _rowcount(result) == 0:
            raise NotFoundError("approval not found")
        return approval

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        row = await self._one(select(ApprovalRow).where(ApprovalRow.id == approval_id), "approval")
        return _load(ApprovalRequest, row.document)

    async def list_approvals(
        self,
        *,
        status: ApprovalStatus | None = None,
        incident_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        stmt = select(ApprovalRow)
        if status is not None:
            stmt = stmt.where(ApprovalRow.status == status.value)
        if incident_id is not None:
            stmt = stmt.where(ApprovalRow.incident_id == incident_id)
        rows = (
            (await self.s.execute(stmt.order_by(ApprovalRow.requested_at.desc()).limit(limit)))
            .scalars()
            .all()
        )
        return [_load(ApprovalRequest, r.document) for r in rows]


class PostgresAuditRepository(_Repo):
    async def append(self, event: AuditEvent) -> AuditEvent:
        await self.s.execute(
            insert(AuditEventRow).values(
                id=event.id,
                at=event.at,
                event_type=event.event_type,
                actor_id=event.actor.id,
                incident_id=event.incident_id,
                tool_name=event.tool_name,
                decision=event.decision,
                document=_doc(event),
            )
        )
        return event

    async def list_events(
        self, *, incident_id: uuid.UUID | None = None, limit: int = 200, offset: int = 0
    ) -> list[AuditEvent]:
        stmt = select(AuditEventRow)
        if incident_id is not None:
            stmt = stmt.where(AuditEventRow.incident_id == incident_id)
        rows = (
            (
                await self.s.execute(
                    stmt.order_by(AuditEventRow.at, AuditEventRow.id).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_load(AuditEvent, r.document) for r in rows]


class PostgresAgentRunRepository(_Repo):
    async def add(self, run: AgentRun) -> AgentRun:
        await self.s.execute(
            insert(AgentRunRow).values(
                id=run.id,
                incident_id=run.incident_id,
                phase=run.phase,
                status=run.status.value,
                started_at=run.started_at,
                document=_doc(run),
            )
        )
        return run

    async def save(self, run: AgentRun) -> AgentRun:
        result = await self.s.execute(
            update(AgentRunRow)
            .where(AgentRunRow.id == run.id)
            .values(status=run.status.value, phase=run.phase, document=_doc(run))
        )
        if _rowcount(result) == 0:
            raise NotFoundError("agent run not found")
        return run

    async def get(self, run_id: uuid.UUID) -> AgentRun:
        row = await self._one(select(AgentRunRow).where(AgentRunRow.id == run_id), "agent run")
        return _load(AgentRun, row.document)

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[AgentRun]:
        rows = (
            (
                await self.s.execute(
                    select(AgentRunRow)
                    .where(AgentRunRow.incident_id == incident_id)
                    .order_by(AgentRunRow.started_at)
                )
            )
            .scalars()
            .all()
        )
        return [_load(AgentRun, r.document) for r in rows]

    async def list_recent(self, limit: int = 50) -> list[AgentRun]:
        rows = (
            (
                await self.s.execute(
                    select(AgentRunRow).order_by(AgentRunRow.started_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_load(AgentRun, r.document) for r in rows]

    async def add_step(self, step: AgentStep) -> AgentStep:
        try:
            async with self.s.begin_nested():
                await self.s.execute(
                    insert(AgentStepRow).values(
                        id=step.id,
                        agent_run_id=step.agent_run_id,
                        incident_id=step.incident_id,
                        seq=step.seq,
                        at=step.at,
                        document=_doc(step),
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(
                f"step {step.seq} already recorded for run {step.agent_run_id}"
            ) from exc
        return step

    async def steps_for_run(self, run_id: uuid.UUID) -> list[AgentStep]:
        rows = (
            (
                await self.s.execute(
                    select(AgentStepRow)
                    .where(AgentStepRow.agent_run_id == run_id)
                    .order_by(AgentStepRow.seq)
                )
            )
            .scalars()
            .all()
        )
        return [_load(AgentStep, r.document) for r in rows]

    async def steps_for_incident(self, incident_id: uuid.UUID) -> list[AgentStep]:
        rows = (
            (
                await self.s.execute(
                    select(AgentStepRow)
                    .where(AgentStepRow.incident_id == incident_id)
                    .order_by(AgentStepRow.at, AgentStepRow.seq)
                )
            )
            .scalars()
            .all()
        )
        return [_load(AgentStep, r.document) for r in rows]


class PostgresToolExecutionRepository(_Repo):
    async def add(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        try:
            async with self.s.begin_nested():
                await self.s.execute(
                    insert(ToolExecutionRow).values(
                        id=record.id,
                        incident_id=record.incident_id,
                        agent_run_id=record.agent_run_id,
                        action_plan_id=record.action_plan_id,
                        tool_name=record.tool_name,
                        status=record.status.value,
                        idempotency_key=record.idempotency_key,
                        created_at=record.created_at,
                        document=_doc(record),
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(f"tool execution with key {record.idempotency_key} exists") from exc
        return record

    async def save(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        result = await self.s.execute(
            update(ToolExecutionRow)
            .where(ToolExecutionRow.id == record.id)
            .values(status=record.status.value, document=_doc(record))
        )
        if _rowcount(result) == 0:
            raise NotFoundError("tool execution not found")
        return record

    async def get(self, execution_id: uuid.UUID) -> ToolExecutionRecord:
        row = await self._one(
            select(ToolExecutionRow).where(ToolExecutionRow.id == execution_id), "tool execution"
        )
        return _load(ToolExecutionRecord, row.document)

    async def find_by_key(self, idempotency_key: str) -> ToolExecutionRecord | None:
        row = (
            await self.s.execute(
                select(ToolExecutionRow).where(ToolExecutionRow.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        return _load(ToolExecutionRecord, row.document) if row else None

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[ToolExecutionRecord]:
        rows = (
            (
                await self.s.execute(
                    select(ToolExecutionRow)
                    .where(ToolExecutionRow.incident_id == incident_id)
                    .order_by(ToolExecutionRow.created_at, ToolExecutionRow.id)
                )
            )
            .scalars()
            .all()
        )
        return [_load(ToolExecutionRecord, r.document) for r in rows]

    async def count_for_run(self, agent_run_id: uuid.UUID) -> int:
        return int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(ToolExecutionRow)
                    .where(ToolExecutionRow.agent_run_id == agent_run_id)
                )
            ).scalar_one()
        )


class PostgresMemoryRepository(_Repo):
    async def add(self, memory: IncidentMemory) -> IncidentMemory:
        doc = _doc(memory)
        doc["embedding"] = None  # vectors live in their own column
        try:
            async with self.s.begin_nested():
                await self.s.execute(
                    insert(IncidentMemoryRow).values(
                        id=memory.id,
                        incident_id=memory.incident_id,
                        root_cause_service=memory.root_cause_service,
                        outcome=memory.outcome,
                        created_at=memory.created_at,
                        embedding_text=memory.embedding_text,
                        embedding=memory.embedding,
                        document=doc,
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(f"memory for incident {memory.incident_id} exists") from exc
        return memory

    @staticmethod
    def _to_domain(row: IncidentMemoryRow, *, with_embedding: bool = False) -> IncidentMemory:
        doc = dict(row.document)
        doc["embedding"] = (
            list(row.embedding) if (with_embedding and row.embedding is not None) else None
        )
        return _load(IncidentMemory, doc)

    async def get_for_incident(self, incident_id: uuid.UUID) -> IncidentMemory | None:
        row = (
            await self.s.execute(
                select(IncidentMemoryRow).where(IncidentMemoryRow.incident_id == incident_id)
            )
        ).scalar_one_or_none()
        return self._to_domain(row, with_embedding=True) if row else None

    async def list_memories(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[IncidentMemory], int]:
        rows = (
            (
                await self.s.execute(
                    select(IncidentMemoryRow)
                    .order_by(IncidentMemoryRow.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = (
            await self.s.execute(select(func.count()).select_from(IncidentMemoryRow))
        ).scalar_one()
        return [self._to_domain(r) for r in rows], int(total)

    async def search(
        self,
        embedding: list[float],
        *,
        limit: int = 5,
        exclude_incident_id: uuid.UUID | None = None,
    ) -> list[MemoryMatch]:
        distance = IncidentMemoryRow.embedding.cosine_distance(embedding)
        stmt = select(IncidentMemoryRow, distance.label("distance")).where(
            IncidentMemoryRow.embedding.is_not(None)
        )
        if exclude_incident_id is not None:
            stmt = stmt.where(IncidentMemoryRow.incident_id != exclude_incident_id)
        rows = (await self.s.execute(stmt.order_by(distance).limit(limit))).all()
        return [
            MemoryMatch(memory=self._to_domain(row), similarity=max(0.0, 1.0 - float(dist)))
            for row, dist in rows
        ]

    async def search_lexical(
        self,
        terms: Sequence[str],
        services: Sequence[str],
        *,
        limit: int = 5,
        exclude_incident_id: uuid.UUID | None = None,
    ) -> list[MemoryMatch]:
        """Full-text fallback for when no embedding provider is configured.

        Uses the GIN index on ``to_tsvector('english', embedding_text)`` and ranks with
        ``ts_rank``, so the fallback orders results rather than merely filtering them. Terms are
        OR-ed: an incident that matches more of them ranks higher.
        """
        words = _tsquery_words([*terms, *services])
        if not words:
            return []
        query = func.to_tsquery("english", " | ".join(words))
        document = func.to_tsvector("english", IncidentMemoryRow.embedding_text)
        rank = func.ts_rank(document, query)
        stmt = select(IncidentMemoryRow, rank.label("rank")).where(document.op("@@")(query))
        if exclude_incident_id is not None:
            stmt = stmt.where(IncidentMemoryRow.incident_id != exclude_incident_id)
        rows = (await self.s.execute(stmt.order_by(rank.desc()).limit(limit * 4))).all()
        matches: list[MemoryMatch] = []
        for row, rank_value in rows:
            memory = self._to_domain(row)
            overlap = len(set(services) & set(memory.affected_services))
            # ts_rank is unbounded but small; squash it into 0..1 and add a service-overlap bonus
            score = min(1.0, float(rank_value) * 8.0 + 0.1 * overlap)
            matches.append(MemoryMatch(memory=memory, similarity=score, matched_on="lexical"))
        matches.sort(key=lambda m: -m.similarity)
        return matches[:limit]


class PostgresNotificationRepository(_Repo):
    async def add(self, notification: Notification) -> Notification:
        await self.s.execute(
            insert(NotificationRow).values(
                id=notification.id,
                kind=notification.kind.value,
                incident_id=notification.incident_id,
                at=notification.at,
                read=notification.read,
                document=_doc(notification),
            )
        )
        return notification

    async def list_notifications(
        self, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        stmt = select(NotificationRow)
        if unread_only:
            stmt = stmt.where(NotificationRow.read.is_(False))
        rows = (
            (await self.s.execute(stmt.order_by(NotificationRow.at.desc()).limit(limit)))
            .scalars()
            .all()
        )
        return [_load(Notification, {**r.document, "read": r.read}) for r in rows]

    async def mark_read(self, notification_id: uuid.UUID) -> None:
        await self.s.execute(
            update(NotificationRow).where(NotificationRow.id == notification_id).values(read=True)
        )
