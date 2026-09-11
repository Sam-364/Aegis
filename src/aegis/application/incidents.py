"""Incident queries and human lifecycle actions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from aegis.application.events import Emitter
from aegis.domain.action import ActionPlan
from aegis.domain.agent import AgentRun, AgentStep
from aegis.domain.approval import ApprovalRequest
from aegis.domain.audit import AuditEvent
from aegis.domain.base import Actor
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import IncidentStatus, Severity
from aegis.domain.events import EventType
from aegis.domain.evidence import EvidenceGraph
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident, IncidentEvent
from aegis.domain.statemachine import transition
from aegis.domain.tool import ToolExecutionRecord
from aegis.evidence.service import EvidenceService
from aegis.ports.messaging import EventPublisher, WorkflowController
from aegis.ports.repositories import UnitOfWorkFactory


@dataclass
class IncidentDetail:
    incident: Incident
    hypotheses: list[Hypothesis] = field(default_factory=list)
    action_plans: list[ActionPlan] = field(default_factory=list)
    approvals: list[ApprovalRequest] = field(default_factory=list)
    agent_runs: list[AgentRun] = field(default_factory=list)
    evidence: EvidenceGraph | None = None
    tool_executions: list[ToolExecutionRecord] = field(default_factory=list)
    recent_events: list[IncidentEvent] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident": self.incident.model_dump(mode="json"),
            "hypotheses": [h.model_dump(mode="json") for h in self.hypotheses],
            "action_plans": [p.model_dump(mode="json") for p in self.action_plans],
            "approvals": [a.model_dump(mode="json") for a in self.approvals],
            "agent_runs": [r.model_dump(mode="json") for r in self.agent_runs],
            "evidence": self.evidence.model_dump(mode="json") if self.evidence else None,
            "tool_executions": [t.model_dump(mode="json") for t in self.tool_executions],
            "recent_events": [e.model_dump(mode="json") for e in self.recent_events],
        }


class IncidentService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        publisher: EventPublisher | None = None,
        workflows: WorkflowController | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.emitter = Emitter(publisher)
        self.workflows = workflows
        self.clock = clock or SystemClock()

    async def list_incidents(
        self,
        *,
        status: Sequence[IncidentStatus] | None = None,
        severity: Sequence[Severity] | None = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Incident], int]:
        async with self.uow_factory() as uow:
            return await uow.incidents.list_incidents(
                status=status,
                severity=severity,
                active_only=active_only,
                limit=limit,
                offset=offset,
            )

    async def get(self, incident_id: uuid.UUID) -> Incident:
        async with self.uow_factory() as uow:
            return await uow.incidents.get(incident_id)

    async def detail(self, incident_id: uuid.UUID) -> IncidentDetail:
        async with self.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            return IncidentDetail(
                incident=incident,
                hypotheses=await uow.hypotheses.list_for_incident(incident_id),
                action_plans=await uow.action_plans.list_for_incident(incident_id),
                approvals=await uow.approvals.list_approvals(incident_id=incident_id),
                agent_runs=await uow.agent_runs.list_for_incident(incident_id),
                evidence=await EvidenceService(uow.evidence).graph(incident.id),
                tool_executions=await uow.tool_executions.list_for_incident(incident_id),
                recent_events=(await uow.incidents.events(incident_id, limit=1000))[-50:],
            )

    async def timeline(
        self, incident_id: uuid.UUID, after_seq: int = 0, limit: int = 500
    ) -> list[IncidentEvent]:
        async with self.uow_factory() as uow:
            await uow.incidents.get(incident_id)
            return await uow.incidents.events(incident_id, after_seq=after_seq, limit=limit)

    async def evidence(self, incident_id: uuid.UUID) -> EvidenceGraph:
        async with self.uow_factory() as uow:
            await uow.incidents.get(incident_id)
            return await EvidenceService(uow.evidence).graph(incident_id)  # type: ignore[arg-type]

    async def hypotheses(self, incident_id: uuid.UUID) -> list[Hypothesis]:
        async with self.uow_factory() as uow:
            return await uow.hypotheses.list_for_incident(incident_id)

    async def actions(self, incident_id: uuid.UUID) -> list[ActionPlan]:
        async with self.uow_factory() as uow:
            return await uow.action_plans.list_for_incident(incident_id)

    async def audit(
        self, incident_id: uuid.UUID | None, limit: int = 200, offset: int = 0
    ) -> list[AuditEvent]:
        async with self.uow_factory() as uow:
            return await uow.audit.list_events(incident_id=incident_id, limit=limit, offset=offset)

    async def agent_runs(self, incident_id: uuid.UUID) -> list[AgentRun]:
        async with self.uow_factory() as uow:
            return await uow.agent_runs.list_for_incident(incident_id)

    async def agent_steps(self, run_id: uuid.UUID) -> list[AgentStep]:
        async with self.uow_factory() as uow:
            return await uow.agent_runs.steps_for_run(run_id)

    async def recent_agent_runs(self, limit: int = 50) -> list[AgentRun]:
        async with self.uow_factory() as uow:
            return await uow.agent_runs.list_recent(limit)

    async def tool_executions(self, incident_id: uuid.UUID) -> list[ToolExecutionRecord]:
        async with self.uow_factory() as uow:
            return await uow.tool_executions.list_for_incident(incident_id)

    async def stats(self) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            counts = await uow.incidents.count_by_status()
            items, total = await uow.incidents.list_incidents(limit=200)
            resolved = [i for i in items if i.resolved_at is not None]
            mttr = (
                (sum(i.duration_seconds() for i in resolved) / len(resolved)) if resolved else None
            )
            return {
                "by_status": counts,
                "total": total,
                "active": sum(1 for i in items if i.is_active),
                "resolved": len(resolved),
                "mttr_seconds": mttr,
            }

    # --- human actions ----------------------------------------------------------------------------

    async def acknowledge(self, incident_id: uuid.UUID, actor: Actor) -> Incident:
        async with self.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            if incident.acknowledged_at is None:
                incident.acknowledged_at = self.clock.now()
                await uow.incidents.save(incident)
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.INCIDENT_STATUS_CHANGED,
                    actor=actor,
                    title=f"Acknowledged by {actor.display_name or actor.id}",
                    payload={"acknowledged": True},
                )
                await self.emitter.audit(
                    uow,
                    event_type="incident.acknowledged",
                    actor=actor,
                    incident_id=incident.id,
                    action="acknowledge",
                    decision="ok",
                )
            await uow.commit()
            return incident

    async def change_status(
        self, incident_id: uuid.UUID, target: IncidentStatus, actor: Actor, reason: str = ""
    ) -> Incident:
        """Human-initiated transitions (close, resolve from escalation, reopen)."""
        async with self.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            previous = transition(incident, target, actor, now=self.clock.now())
            if target is IncidentStatus.RESOLVED:
                incident.resolution_summary = reason or "resolved by operator"
            await uow.incidents.save(incident)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=_event_for(target),
                actor=actor,
                title=f"{previous.value} → {target.value} by {actor.display_name or actor.id}"
                + (f": {reason}" if reason else ""),
                payload={"from": previous.value, "to": target.value, "reason": reason},
            )
            await self.emitter.audit(
                uow,
                event_type="incident.status_changed",
                actor=actor,
                incident_id=incident.id,
                action=target.value,
                decision="ok",
                reason=reason,
                data={"from": previous.value, "to": target.value},
            )
            workflow_id = incident.workflow_id
            await uow.commit()
        if (
            target is IncidentStatus.CLOSED
            and self.workflows is not None
            and workflow_id
            and previous.is_active
        ):
            await self.workflows.signal_cancel(workflow_id, reason or "closed by operator")
        return incident


def _event_for(target: IncidentStatus) -> EventType:
    return {
        IncidentStatus.RESOLVED: EventType.INCIDENT_RESOLVED,
        IncidentStatus.CLOSED: EventType.INCIDENT_CLOSED,
        IncidentStatus.ESCALATED: EventType.INCIDENT_ESCALATED,
        IncidentStatus.FAILED: EventType.INCIDENT_FAILED,
    }.get(target, EventType.INCIDENT_STATUS_CHANGED)
