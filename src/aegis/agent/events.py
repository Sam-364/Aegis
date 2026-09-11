"""Timeline emission from inside the agent loop."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from aegis.domain.base import Actor
from aegis.domain.errors import AegisError
from aegis.domain.events import EventType
from aegis.domain.ids import IncidentId
from aegis.domain.incident import IncidentEvent
from aegis.ports.messaging import EventPublisher
from aegis.ports.repositories import IncidentRepository


async def emit(
    incidents: IncidentRepository,
    publisher: EventPublisher | None,
    *,
    incident_id: uuid.UUID,
    type: EventType,  # noqa: A002 - mirrors the event field name
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
    stored = await incidents.append_event(event)
    if publisher is not None:
        with contextlib.suppress(AegisError, OSError):
            await publisher.publish(stored)
    return stored
