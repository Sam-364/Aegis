"""In-process adapters: call the simulation engine directly. Used by unit tests and evals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegis.domain.errors import InfrastructureError
from aegis.domain.telemetry import (
    Deployment,
    LogEntry,
    MetricSeries,
    ResourceInfo,
    ServiceHealth,
    Topology,
    TraceSummary,
)
from aegis.infrastructure.simulator.convert import (
    to_deployment,
    to_health,
    to_log,
    to_resource,
    to_series,
    to_topology,
    to_trace,
)
from aegis.simulator.engine import SimulationEngine, SimulationError


def _ts(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


class InProcessSimulatorTelemetry:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    async def topology(self) -> Topology:
        return to_topology(self.engine.topology())

    async def services(self) -> list[str]:
        return self.engine.component_names()

    async def metric_names(self) -> list[str]:
        return self.engine.metric_names()

    async def metrics(
        self, service: str, metric: str, *, start: datetime, end: datetime
    ) -> MetricSeries:
        try:
            samples = self.engine.series(
                service, metric, start=start.timestamp(), end=end.timestamp()
            )
        except SimulationError as exc:
            raise InfrastructureError(str(exc)) from exc
        return to_series(service, metric, [{"at": s.at, "value": s.value} for s in samples])

    async def snapshot(self, *, window_seconds: int) -> list[MetricSeries]:
        out: list[MetricSeries] = []
        for component, metrics in self.engine.snapshot(window_seconds).items():
            for metric, samples in metrics.items():
                out.append(
                    to_series(component, metric, [{"at": s.at, "value": s.value} for s in samples])
                )
        return out

    async def logs(
        self,
        service: str,
        *,
        start: datetime,
        end: datetime,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        items = self.engine.logs_for(
            service or None, start=start.timestamp(), end=end.timestamp(), level=level, limit=limit
        )
        return [
            to_log(
                {
                    "at": i.at,
                    "service": i.service,
                    "level": i.level,
                    "message": i.message,
                    "attributes": i.attributes,
                }
            )
            for i in items
        ]

    async def traces(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        errors_only: bool = False,
        limit: int = 20,
    ) -> list[TraceSummary]:
        items = self.engine.traces_for(
            service,
            start=start.timestamp(),
            end=end.timestamp(),
            errors_only=errors_only,
            limit=limit,
        )
        return [
            to_trace(
                {
                    "trace_id": t.trace_id,
                    "at": t.at,
                    "root_service": t.root_service,
                    "duration_ms": t.duration_ms,
                    "error": t.error,
                    "spans": [
                        {
                            "service": s.service,
                            "operation": s.operation,
                            "duration_ms": s.duration_ms,
                            "error": s.error,
                            "depth": s.depth,
                        }
                        for s in t.spans
                    ],
                }
            )
            for t in items
        ]

    async def health(self, service: str) -> ServiceHealth:
        try:
            return to_health(self.engine.health(service))
        except SimulationError as exc:
            raise InfrastructureError(str(exc)) from exc

    async def deployments(self, service: str | None = None) -> list[Deployment]:
        return [
            to_deployment(
                {
                    "service": d.service,
                    "version": d.version,
                    "previous_version": d.previous_version,
                    "deployed_at": d.deployed_at,
                    "change_summary": d.change_summary,
                }
            )
            for d in self.engine.deployments_for(service)
        ]

    async def resource(self, component: str) -> ResourceInfo:
        try:
            return to_resource(self.engine.resource(component))
        except SimulationError as exc:
            raise InfrastructureError(str(exc)) from exc

    async def baseline(self, service: str, metric: str) -> float | None:
        return self.engine.baseline(service, metric)


class InProcessSimulatorGateway:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = fn(*args, **kwargs)
            return result
        except SimulationError as exc:
            raise InfrastructureError(str(exc)) from exc

    async def restart_service(self, service: str, *, reason: str) -> dict[str, Any]:
        return self._call(self.engine.restart, service, reason=reason)

    async def rollback_deployment(
        self, service: str, *, to_version: str | None, reason: str
    ) -> dict[str, Any]:
        return self._call(self.engine.rollback, service, to_version=to_version, reason=reason)

    async def scale_service(self, service: str, *, replicas: int, reason: str) -> dict[str, Any]:
        return self._call(self.engine.scale, service, replicas=replicas, reason=reason)

    async def clear_cache(self, component: str, *, reason: str) -> dict[str, Any]:
        return self._call(self.engine.clear_cache, component, reason=reason)

    async def rotate_connection_pool(
        self, service: str, *, target: str, reason: str
    ) -> dict[str, Any]:
        return self._call(self.engine.rotate_pool, service, target=target, reason=reason)

    async def run_diagnostic(
        self, kind: str, *, target: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(self.engine.diagnostic, kind, target, parameters)
