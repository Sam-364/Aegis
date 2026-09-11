"""HTTP adapters talking to the simulator process. Bounded timeouts, typed errors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

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


class _Http:
    def __init__(
        self, base_url: str, client: httpx.AsyncClient | None = None, timeout: float = 10.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self._client.get(path, params=clean)
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"simulator unreachable: {exc}") from exc
        return self._result(r)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            r = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"simulator unreachable: {exc}") from exc
        return self._result(r)

    @staticmethod
    def _result(r: httpx.Response) -> Any:
        if r.status_code >= 400:
            detail = (
                r.json().get("detail", r.text)
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            )
            raise InfrastructureError(
                f"simulator error {r.status_code}: {detail}", details={"status": r.status_code}
            )
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()


class HttpSimulatorTelemetry(_Http):
    async def topology(self) -> Topology:
        return to_topology(await self.get("/api/topology"))

    async def services(self) -> list[str]:
        data = await self.get("/api/components")
        return list(data["components"])

    async def metric_names(self) -> list[str]:
        data = await self.get("/api/metrics/names")
        return list(data["metrics"])

    async def metrics(
        self, service: str, metric: str, *, start: datetime, end: datetime
    ) -> MetricSeries:
        data = await self.get(
            f"/api/components/{service}/metrics",
            metric=metric,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        return to_series(service, metric, data["samples"])

    async def snapshot(self, *, window_seconds: int) -> list[MetricSeries]:
        data = await self.get("/api/metrics/snapshot", window_seconds=window_seconds)
        return [
            to_series(component, metric, samples)
            for component, metrics in data["components"].items()
            for metric, samples in metrics.items()
        ]

    async def logs(
        self,
        service: str,
        *,
        start: datetime,
        end: datetime,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        data = await self.get(
            "/api/logs",
            component=service or None,
            start=start.isoformat(),
            end=end.isoformat(),
            level=level,
            limit=limit,
        )
        return [to_log(i) for i in data["logs"]]

    async def traces(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        errors_only: bool = False,
        limit: int = 20,
    ) -> list[TraceSummary]:
        data = await self.get(
            "/api/traces",
            component=service,
            start=start.isoformat(),
            end=end.isoformat(),
            errors_only=errors_only,
            limit=limit,
        )
        return [to_trace(i) for i in data["traces"]]

    async def health(self, service: str) -> ServiceHealth:
        return to_health(await self.get(f"/api/components/{service}/health"))

    async def deployments(self, service: str | None = None) -> list[Deployment]:
        data = await self.get("/api/deployments", service=service)
        return [to_deployment(i) for i in data["deployments"]]

    async def resource(self, component: str) -> ResourceInfo:
        return to_resource(await self.get(f"/api/components/{component}/resource"))

    async def baseline(self, service: str, metric: str) -> float | None:
        data = await self.get(f"/api/components/{service}/baseline/{metric}")
        value = data.get("baseline")
        return float(value) if value is not None else None


class HttpSimulatorGateway(_Http):
    async def restart_service(self, service: str, *, reason: str) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/actions/restart", {"target": service, "reason": reason}
        )
        return result

    async def rollback_deployment(
        self, service: str, *, to_version: str | None, reason: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/actions/rollback",
            {"service": service, "to_version": to_version, "reason": reason},
        )
        return result

    async def scale_service(self, service: str, *, replicas: int, reason: str) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/actions/scale", {"service": service, "replicas": replicas, "reason": reason}
        )
        return result

    async def clear_cache(self, component: str, *, reason: str) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/actions/clear-cache", {"component": component, "reason": reason}
        )
        return result

    async def rotate_connection_pool(
        self, service: str, *, target: str, reason: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/actions/rotate-pool", {"service": service, "target": target, "reason": reason}
        )
        return result

    async def run_diagnostic(
        self, kind: str, *, target: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self.post(
            "/api/diagnostics", {"kind": kind, "target": target, "parameters": parameters}
        )
        return result
