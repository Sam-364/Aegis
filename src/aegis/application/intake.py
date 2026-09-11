"""Incident intake: what the detector calls. Creates incidents and starts durable workflows."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from aegis.application.events import Emitter
from aegis.domain.base import Actor
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import Environment, NotificationKind, Severity
from aegis.domain.events import EventType
from aegis.domain.incident import AnomalySignal, Incident
from aegis.flow.registry import FlowRegistry
from aegis.logging import get_logger
from aegis.ports.messaging import EventPublisher, WorkflowController
from aegis.ports.repositories import UnitOfWorkFactory
from aegis.telemetry.metrics import INCIDENTS_TOTAL

log = get_logger(__name__)


class IncidentIntakeService:
    """Implements :class:`aegis.ports.detection.IncidentSink`."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        flows: FlowRegistry,
        *,
        publisher: EventPublisher | None = None,
        workflows: WorkflowController | None = None,
        environment: Environment = Environment.DEVELOPMENT,
        tenant_id: str = "default",
        clock: Clock | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.flows = flows
        self.emitter = Emitter(publisher)
        self.workflows = workflows
        self.environment = environment
        self.tenant_id = tenant_id
        self.clock = clock or SystemClock()

    async def open_incidents(self) -> list[Incident]:
        async with self.uow_factory() as uow:
            items, _ = await uow.incidents.list_incidents(active_only=True, limit=200)
            return items

    async def open_incident(
        self,
        *,
        title: str,
        summary: str,
        severity: Severity,
        signals: Sequence[AnomalySignal],
        affected_services: Sequence[str],
        correlation_key: str,
    ) -> Incident:
        flow = self.flows.select(list(signals))
        detected_at = min((s.detected_at for s in signals), default=self.clock.now())
        incident = Incident(
            title=title,
            summary=summary,
            severity=severity,
            signals=list(signals),
            affected_services=list(affected_services),
            correlation_key=correlation_key,
            flow_name=flow.name,
            flow_version=flow.version,
            detected_at=detected_at,
            environment=self.environment,
            tenant_id=self.tenant_id,
        )
        actor = Actor.detector()
        async with self.uow_factory() as uow:
            incident = await uow.incidents.add(incident)
            INCIDENTS_TOTAL.labels(severity.value, flow.name).inc()
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.INCIDENT_DETECTED,
                actor=actor,
                title=f"Anomaly detected: {title}",
                payload={
                    "signals": [s.model_dump(mode="json") for s in signals],
                    "severity": severity.value,
                    "affected_services": list(affected_services),
                },
            )
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.INCIDENT_CREATED,
                actor=actor,
                title=f"{incident.display_id} created ({severity.value.upper()})",
                payload={"incident": _incident_payload(incident)},
            )
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.FLOW_SELECTED,
                actor=actor,
                title=f"Flow selected: {flow.ref}",
                payload={
                    "flow": flow.name,
                    "version": flow.version,
                    "phases": [p.name for p in flow.phases],
                },
            )
            await self.emitter.audit(
                uow,
                event_type="incident.created",
                actor=actor,
                incident_id=incident.id,
                action="create",
                decision="created",
                reason=summary,
                data={"severity": severity.value, "flow": flow.ref},
            )
            await self.emitter.notify(
                uow,
                kind=NotificationKind.INCIDENT_DETECTED,
                incident_id=incident.id,
                title=f"{incident.display_id} {severity.value.upper()}: {title}",
                body=summary,
                data={"severity": severity.value},
            )
            await uow.commit()
        if self.workflows is not None:
            try:
                workflow_id = await self.workflows.start_incident_workflow(incident.id)
            except Exception as exc:
                log.error(
                    "intake.workflow_start_failed", incident_id=str(incident.id), error=str(exc)
                )
                workflow_id = None
            if workflow_id:
                async with self.uow_factory() as uow:
                    fresh = await uow.incidents.get(incident.id)
                    fresh.workflow_id = workflow_id
                    await uow.incidents.save(fresh)
                    await uow.commit()
                incident.workflow_id = workflow_id
        log.info(
            "intake.incident_opened",
            incident_id=str(incident.id),
            number=incident.number,
            severity=severity.value,
            flow=flow.ref,
            workflow_id=incident.workflow_id,
        )
        return incident

    async def attach_signals(
        self, incident_id: uuid.UUID, signals: Sequence[AnomalySignal]
    ) -> Incident:
        async with self.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            new = [s for s in signals if all(x.id != s.id for x in incident.signals)]
            for s in new:
                incident.attach_signal(s)
            await uow.incidents.save(incident)
            if new:
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.INCIDENT_SIGNAL_ATTACHED,
                    actor=Actor.detector(),
                    title=f"{len(new)} new signal(s): "
                    + ", ".join(f"{s.service} {s.metric}" for s in new[:4]),
                    payload={
                        "signals": [s.model_dump(mode="json") for s in new],
                        "affected_services": incident.affected_services,
                    },
                )
            await uow.commit()
            return incident


def _incident_payload(incident: Incident) -> dict[str, object]:
    return {
        "id": str(incident.id),
        "number": incident.number,
        "title": incident.title,
        "severity": incident.severity.value,
        "status": incident.status.value,
        "affected_services": incident.affected_services,
        "flow": incident.flow_name,
    }
