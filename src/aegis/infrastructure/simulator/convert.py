"""Shared conversion from simulator JSON/records to domain telemetry objects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegis.domain.enums import HealthState
from aegis.domain.telemetry import (
    DependencyEdge,
    Deployment,
    LogEntry,
    MetricSample,
    MetricSeries,
    ResourceInfo,
    ServiceHealth,
    ServiceNode,
    Topology,
    TraceSpanSummary,
    TraceSummary,
)

METRIC_UNITS = {
    "latency_p50_ms": "ms",
    "latency_p95_ms": "ms",
    "error_rate": "ratio",
    "request_rate": "rps",
    "cpu_percent": "%",
    "memory_percent": "%",
    "db_pool_wait_ms": "ms",
    "redis_pool_wait_ms": "ms",
    "connections": "count",
    "saturation": "ratio",
    "ops_per_sec": "ops",
    "hit_rate": "ratio",
}


def parse_ts(value: str | float) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_series(component: str, metric: str, samples: list[dict[str, Any]]) -> MetricSeries:
    return MetricSeries(
        service=component,
        metric=metric,
        unit=METRIC_UNITS.get(metric, ""),
        samples=tuple(MetricSample(at=parse_ts(s["at"]), value=float(s["value"])) for s in samples),
    )


def to_topology(data: dict[str, Any]) -> Topology:
    return Topology(
        nodes=tuple(
            ServiceNode(
                name=n["name"],
                kind=n["kind"],
                tier=int(n.get("tier", 0)),
                owner=n.get("owner", ""),
                replicas=int(n.get("replicas", 1)),
                version=n.get("version", ""),
            )
            for n in data["nodes"]
        ),
        edges=tuple(
            DependencyEdge(
                source=e["source"],
                target=e["target"],
                protocol=e.get("protocol", "http"),
                critical=bool(e.get("critical", True)),
            )
            for e in data["edges"]
        ),
    )


def to_log(item: dict[str, Any]) -> LogEntry:
    return LogEntry(
        at=parse_ts(item["at"]),
        service=item["service"],
        level=item["level"],
        message=item["message"],
        attributes=dict(item.get("attributes", {})),
    )


def to_trace(item: dict[str, Any]) -> TraceSummary:
    return TraceSummary(
        trace_id=item["trace_id"],
        at=parse_ts(item["at"]),
        root_service=item["root_service"],
        duration_ms=float(item["duration_ms"]),
        error=bool(item["error"]),
        spans=tuple(
            TraceSpanSummary(
                service=s["service"],
                operation=s["operation"],
                duration_ms=float(s["duration_ms"]),
                error=bool(s["error"]),
                depth=int(s.get("depth", 0)),
            )
            for s in item.get("spans", [])
        ),
    )


def to_health(item: dict[str, Any]) -> ServiceHealth:
    return ServiceHealth(
        service=item["service"],
        state=HealthState(item["state"]),
        checks=dict(item.get("checks", {})),
        message=item.get("message", ""),
        at=parse_ts(item["at"]),
    )


def to_deployment(item: dict[str, Any]) -> Deployment:
    return Deployment(
        service=item["service"],
        version=item["version"],
        deployed_at=parse_ts(item["deployed_at"]),
        previous_version=item.get("previous_version"),
        change_summary=item.get("change_summary", ""),
        rollback_available=item.get("previous_version") is not None,
    )


def to_resource(item: dict[str, Any]) -> ResourceInfo:
    return ResourceInfo(
        component=item["component"],
        kind=item["kind"],
        at=parse_ts(item["at"]),
        metrics={k: float(v) for k, v in item.get("metrics", {}).items()},
        attributes={k: str(v) for k, v in item.get("attributes", {}).items()},
        top_clients={k: float(v) for k, v in item.get("top_clients", {}).items()},
    )
