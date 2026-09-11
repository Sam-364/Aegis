"""Event publication, locking, rate limiting and workflow control ports."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, Protocol

from aegis.domain.incident import IncidentEvent


class EventPublisher(Protocol):
    """Fan-out of incident events to live subscribers (SSE)."""

    async def publish(self, event: IncidentEvent) -> None: ...
    def subscribe(self, incident_id: uuid.UUID | None = None) -> AsyncIterator[IncidentEvent]: ...


class LeaderLock(Protocol):
    async def acquire(self, name: str, ttl: timedelta) -> bool: ...
    async def renew(self, name: str, ttl: timedelta) -> bool: ...
    async def release(self, name: str) -> None: ...


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]: ...


class WorkflowController(Protocol):
    """Starts and signals durable incident workflows. Hides the Temporal client."""

    async def start_incident_workflow(self, incident_id: uuid.UUID) -> str: ...
    async def signal_approval(
        self, workflow_id: str, approval_id: uuid.UUID, approved: bool, decided_by: str, reason: str
    ) -> None: ...
    async def signal_cancel(self, workflow_id: str, reason: str) -> None: ...
    async def describe(self, workflow_id: str) -> dict[str, Any]: ...


class NotificationSink(Protocol):
    async def notify(
        self,
        kind: str,
        *,
        title: str,
        body: str,
        incident_id: uuid.UUID | None,
        data: dict[str, Any],
    ) -> None: ...
