"""Read metrics from Prometheus.

Logs, traces, health, deployments and resources come from a fallback provider (the simulator
adapter locally; Loki/Tempo adapters in production)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from aegis.domain.errors import InfrastructureError
from aegis.domain.telemetry import (
    Deployment,
    LogEntry,
    MetricSample,
    MetricSeries,
    ResourceInfo,
    ServiceHealth,
    Topology,
    TraceSummary,
)
from aegis.infrastructure.simulator.convert import METRIC_UNITS
from aegis.ports.telemetry import TelemetryProvider

INFRA_METRICS = {
    "connections",
    "max_connections",
    "saturation",
    "ops_per_sec",
    "blocked_clients",
    "idle_in_transaction",
    "lock_waits",
    "hit_rate",
}


class PrometheusTelemetryProvider:
    def __init__(
        self,
        base_url: str,
        fallback: TelemetryProvider,
        *,
        client: httpx.AsyncClient | None = None,
        step_seconds: int = 5,
        metric_prefix: str = "sim",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback
        self.client = client or httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        self.step = step_seconds
        self.prefix = metric_prefix
        self._infra: set[str] | None = None

    async def _infra_components(self) -> set[str]:
        if self._infra is None:
            topo = await self.fallback.topology()
            self._infra = {n.name for n in topo.nodes if n.kind in ("database", "cache")}
        return self._infra

    async def _selector(self, service: str, metric: str) -> str:
        infra = await self._infra_components()
        if service in infra or metric in INFRA_METRICS:
            return f'{self.prefix}_infra_{metric}{{component="{service}"}}'
        return f'{self.prefix}_service_{metric}{{service="{service}"}}'

    async def _query_range(
        self, query: str, start: datetime, end: datetime
    ) -> list[tuple[float, float]]:
        try:
            r = await self.client.get(
                "/api/v1/query_range",
                params={
                    "query": query,
                    "start": start.timestamp(),
                    "end": end.timestamp(),
                    "step": f"{self.step}s",
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"prometheus unavailable: {exc}") from exc
        data = r.json()
        if data.get("status") != "success":
            raise InfrastructureError(f"prometheus error: {data.get('error', 'unknown')}")
        results = data.get("data", {}).get("result", [])
        if not results:
            return []
        return [(float(ts), float(v)) for ts, v in results[0].get("values", [])]

    async def topology(self) -> Topology:
        return await self.fallback.topology()

    async def services(self) -> list[str]:
        return await self.fallback.services()

    async def metric_names(self) -> list[str]:
        return await self.fallback.metric_names()

    async def metrics(
        self, service: str, metric: str, *, start: datetime, end: datetime
    ) -> MetricSeries:
        points = await self._query_range(await self._selector(service, metric), start, end)
        return MetricSeries(
            service=service,
            metric=metric,
            unit=METRIC_UNITS.get(metric, ""),
            samples=tuple(
                MetricSample(at=datetime.fromtimestamp(ts, tz=start.tzinfo), value=v)
                for ts, v in points
            ),
        )

    async def snapshot(self, *, window_seconds: int) -> list[MetricSeries]:
        # A full snapshot through PromQL would be one query per series; delegate to the fallback,
        # which serves it in one call, and keep Prometheus for targeted queries.
        return await self.fallback.snapshot(window_seconds=window_seconds)

    async def baseline(self, service: str, metric: str) -> float | None:
        """Median over the previous hour excluding the last 15 minutes."""
        end = datetime.now(tz=UTC_TZ) - timedelta(minutes=15)
        start = end - timedelta(minutes=45)
        try:
            points = await self._query_range(await self._selector(service, metric), start, end)
        except InfrastructureError:
            return await self.fallback.baseline(service, metric)
        if not points:
            return await self.fallback.baseline(service, metric)
        values = sorted(v for _, v in points)
        return values[len(values) // 2]

    async def logs(
        self,
        service: str,
        *,
        start: datetime,
        end: datetime,
        level: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        return await self.fallback.logs(service, start=start, end=end, level=level, limit=limit)

    async def traces(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        errors_only: bool = False,
        limit: int = 20,
    ) -> list[TraceSummary]:
        return await self.fallback.traces(
            service=service, start=start, end=end, errors_only=errors_only, limit=limit
        )

    async def health(self, service: str) -> ServiceHealth:
        return await self.fallback.health(service)

    async def deployments(self, service: str | None = None) -> list[Deployment]:
        return await self.fallback.deployments(service)

    async def resource(self, component: str) -> ResourceInfo:
        return await self.fallback.resource(component)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "prometheus",
            "base_url": self.base_url,
            "fallback": type(self.fallback).__name__,
        }


from datetime import UTC as UTC_TZ  # noqa: E402 - placed after use for readability of the class
