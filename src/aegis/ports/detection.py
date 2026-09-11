"""Port through which the detection engine opens and updates incidents."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from aegis.domain.enums import Severity
from aegis.domain.incident import AnomalySignal, Incident


class IncidentSink(Protocol):
    async def open_incidents(self) -> list[Incident]: ...

    async def open_incident(
        self,
        *,
        title: str,
        summary: str,
        severity: Severity,
        signals: Sequence[AnomalySignal],
        affected_services: Sequence[str],
        correlation_key: str,
    ) -> Incident: ...

    async def attach_signals(
        self, incident_id: uuid.UUID, signals: Sequence[AnomalySignal]
    ) -> Incident: ...
