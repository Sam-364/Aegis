"""Execution context handed to tool handlers. Built by the runtime, never by the LLM."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from aegis.domain.base import Actor
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import Environment
from aegis.domain.flow import FlowPack, FlowPhase
from aegis.domain.incident import Incident
from aegis.domain.memory import MemoryMatch, SimilarIncidentQuery
from aegis.ports.telemetry import InfrastructureGateway, TelemetryProvider


class MemorySearch(Protocol):
    async def __call__(self, query: SimilarIncidentQuery) -> list[MemoryMatch]: ...


@dataclass
class ToolContext:
    incident: Incident
    flow: FlowPack
    phase: FlowPhase
    actor: Actor
    environment: Environment
    telemetry: TelemetryProvider
    infrastructure: InfrastructureGateway
    memory_search: MemorySearch | None = None
    clock: Clock = field(default_factory=SystemClock)
    agent_run_id: uuid.UUID | None = None
    action_plan_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    known_components: frozenset[str] = frozenset()
    default_window_seconds: int = 300
    max_window_seconds: int = 1800

    def now(self) -> datetime:
        return self.clock.now()

    def window(self, seconds: int | None = None) -> tuple[datetime, datetime]:
        span = min(seconds or self.default_window_seconds, self.max_window_seconds)
        end = self.now()
        return end - timedelta(seconds=span), end

    def incident_window(self) -> tuple[datetime, datetime]:
        """From two minutes before detection until now."""
        return self.incident.detected_at - timedelta(seconds=120), self.now()
