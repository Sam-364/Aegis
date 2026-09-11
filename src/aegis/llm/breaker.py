"""A small circuit breaker so a failing LLM endpoint degrades the agent to deterministic mode
quickly instead of burning the incident budget on timeouts."""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, *, failures: int = 3, reset_seconds: float = 60.0) -> None:
        self.threshold = failures
        self.reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.reset_seconds:
            self._opened_at = None  # half-open: allow one attempt
            self._failures = self.threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        return {"open": self.open, "failures": self._failures, "threshold": self.threshold}
