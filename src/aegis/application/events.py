"""Timeline + audit + notification emission shared by application services."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from aegis.domain.audit import AuditEvent
from aegis.domain.base import Actor
from aegis.domain.enums import NotificationKind
from aegis.domain.errors import AegisError
from aegis.domain.events import EventType
from aegis.domain.ids import IncidentId
from aegis.domain.incident import IncidentEvent
from aegis.domain.notification import Notification
from aegis.ports.messaging import EventPublisher
from aegis.ports.repositories import UnitOfWork


class Emitter:
    def __init__(self, publisher: EventPublisher | None) -> None:
        self.publisher = publisher

    async def timeline(
        self,
        uow: UnitOfWork,
        *,
        incident_id: uuid.UUID,
        type: EventType,  # noqa: A002
        title: str,
        actor: Actor,
        payload: dict[str, Any] | None = None,
    ) -> IncidentEvent:
        event = IncidentEvent(
            incident_id=IncidentId(incident_id),
            type=type.value,
            actor=actor,
            title=title,
            payload=payload or {},
        )
        stored = await uow.incidents.append_event(event)
        if self.publisher is not None:
            with contextlib.suppress(AegisError, OSError):
                await self.publisher.publish(stored)
        return stored

    async def audit(
        self,
        uow: UnitOfWork,
        *,
        event_type: str,
        actor: Actor,
        incident_id: uuid.UUID | None,
        action: str | None = None,
        decision: str | None = None,
        reason: str = "",
        data: dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> AuditEvent:
        return await uow.audit.append(
            AuditEvent(
                event_type=event_type,
                actor=actor,
                incident_id=incident_id,
                action=action,
                decision=decision,
                reason=reason[:500],
                data=data or {},
                tool_name=tool_name,
            )
        )

    async def notify(
        self,
        uow: UnitOfWork,
        *,
        kind: NotificationKind,
        title: str,
        body: str,
        incident_id: uuid.UUID | None,
        data: dict[str, Any] | None = None,
    ) -> Notification:
        return await uow.notifications.add(
            Notification(
                kind=kind, incident_id=incident_id, title=title, body=body, data=data or {}
            )
        )
