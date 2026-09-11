"""Clock abstraction so that time is injectable in tests and deterministic in simulations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class ManualClock:
    """A clock that only moves when told to. Used by tests and the simulator."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
