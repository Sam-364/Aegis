"""In-process messaging adapters (tests, single-process deployments)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from aegis.domain.incident import IncidentEvent


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self._subscribers: list[tuple[uuid.UUID | None, asyncio.Queue[IncidentEvent]]] = []
        self.published: list[IncidentEvent] = []

    async def publish(self, event: IncidentEvent) -> None:
        self.published.append(event)
        for incident_id, queue in list(self._subscribers):
            if incident_id is None or incident_id == event.incident_id:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    async def subscribe(self, incident_id: uuid.UUID | None = None) -> AsyncIterator[IncidentEvent]:
        queue: asyncio.Queue[IncidentEvent] = asyncio.Queue(maxsize=1000)
        entry = (incident_id, queue)
        self._subscribers.append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(entry)


class InMemoryLeaderLock:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, float]] = {}
        self.token = uuid.uuid4().hex

    async def acquire(self, name: str, ttl: timedelta) -> bool:
        now = time.monotonic()
        holder = self._locks.get(name)
        if holder and holder[1] > now and holder[0] != self.token:
            return False
        self._locks[name] = (self.token, now + ttl.total_seconds())
        return True

    async def renew(self, name: str, ttl: timedelta) -> bool:
        holder = self._locks.get(name)
        if holder and holder[0] == self.token:
            self._locks[name] = (self.token, time.monotonic() + ttl.total_seconds())
            return True
        return False

    async def release(self, name: str) -> None:
        holder = self._locks.get(name)
        if holder and holder[0] == self.token:
            del self._locks[name]


class InMemoryRateLimiter:
    """Fixed-window counter with a bounded key space, so unknown clients cannot grow it forever."""

    def __init__(self, max_keys: int = 4096) -> None:
        self._windows: dict[str, tuple[int, float]] = {}
        self.max_keys = max_keys

    async def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        if len(self._windows) >= self.max_keys:
            for stale in [k for k, (_, reset) in self._windows.items() if reset <= now]:
                del self._windows[stale]
            if len(self._windows) >= self.max_keys:
                self._windows.clear()
        count, reset = self._windows.get(key, (0, now + window_seconds))
        if reset <= now:
            count, reset = 0, now + window_seconds
        count += 1
        self._windows[key] = (count, reset)
        return count <= limit, max(0, limit - count)


class InMemoryBaselineStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def load(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    async def save(self, key: str, state: dict[str, Any], ttl_seconds: int) -> None:
        self.data[key] = state

    async def keys(self, prefix: str) -> list[str]:
        return [k for k in self.data if k.startswith(prefix)]
