"""Telemetry and infrastructure ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from aegis.domain.telemetry import (
    Deployment,
    LogEntry,
    MetricSeries,
    ResourceInfo,
    ServiceHealth,
    Topology,
    TraceSummary,
)


class TelemetryProvider(Protocol):
    """Read-side access to the observed system. Adapters: simulator, Prometheus."""

    async def topology(self) -> Topology: ...
    async def services(self) -> list[str]: ...
    async def metric_names(self) -> list[str]: ...
    async def metrics(
        self, service: str, metric: str, *, start: datetime, end: datetime
    ) -> MetricSeries: ...
    async def snapshot(self, *, window_seconds: int) -> list[MetricSeries]: ...
    async def logs(
        self,
        service: str,
        *,
        start: datetime,
        end: datetime,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]: ...
    async def traces(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        errors_only: bool = False,
        limit: int = 20,
    ) -> list[TraceSummary]: ...
    async def health(self, service: str) -> ServiceHealth: ...
    async def deployments(self, service: str | None = None) -> list[Deployment]: ...
    async def resource(self, component: str) -> ResourceInfo: ...
    async def baseline(self, service: str, metric: str) -> float | None: ...


class InfrastructureGateway(Protocol):
    """Write-side access. Only the tool executor calls this, only after authorization."""

    async def restart_service(self, service: str, *, reason: str) -> dict[str, Any]: ...
    async def rollback_deployment(
        self, service: str, *, to_version: str | None, reason: str
    ) -> dict[str, Any]: ...
    async def scale_service(
        self, service: str, *, replicas: int, reason: str
    ) -> dict[str, Any]: ...
    async def clear_cache(self, component: str, *, reason: str) -> dict[str, Any]: ...
    async def rotate_connection_pool(
        self, service: str, *, target: str, reason: str
    ) -> dict[str, Any]: ...
    async def run_diagnostic(
        self, kind: str, *, target: str, parameters: dict[str, Any]
    ) -> dict[str, Any]: ...


class BaselineStore(Protocol):
    """Persistence for detector baselines so restarts don't lose warm-up."""

    async def load(self, key: str) -> dict[str, Any] | None: ...
    async def save(self, key: str, state: dict[str, Any], ttl_seconds: int) -> None: ...
    async def keys(self, prefix: str) -> Sequence[str]: ...
