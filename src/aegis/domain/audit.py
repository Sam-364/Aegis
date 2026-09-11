"""Immutable audit events. Never updated, never deleted."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Actor, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.ids import AuditEventId, new_id


class AuditEvent(ValueObject):
    id: AuditEventId = Field(default_factory=lambda: AuditEventId(new_id()))
    at: datetime = Field(default_factory=utcnow)
    event_type: str
    actor: Actor
    incident_id: uuid.UUID | None = None
    flow_name: str | None = None
    phase: str | None = None
    tool_name: str | None = None
    action: str | None = None
    decision: str | None = None
    reason: str = ""
    trace_id: str | None = None
    agent_run_id: uuid.UUID | None = None
    tool_execution_id: uuid.UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)
