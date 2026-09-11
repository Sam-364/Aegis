"""In-app notifications (the local implementation of the NotificationService port)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import NotificationKind
from aegis.domain.ids import NotificationId, new_id


class Notification(ValueObject):
    id: NotificationId = Field(default_factory=lambda: NotificationId(new_id()))
    kind: NotificationKind
    incident_id: uuid.UUID | None = None
    title: str
    body: str = ""
    at: datetime = Field(default_factory=utcnow)
    read: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
