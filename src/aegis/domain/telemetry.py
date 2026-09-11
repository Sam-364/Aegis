"""Telemetry value objects: metrics, logs, traces, health and topology.

These are the shapes every ``TelemetryProvider`` adapter must produce, regardless of whether the
data comes from the simulator, Prometheus, Loki or Tempo.
"""

from __future__ import annotations

from datetime import datetime
from statistics import fmean, pstdev

from pydantic import Field

from aegis.domain.base import ValueObject
from aegis.domain.enums import HealthState


class MetricSample(ValueObject):
    at: datetime
    value: float


class MetricSeries(ValueObject):
    """A single metric for a single service over a time window."""

    service: str
    metric: str
    unit: str = ""
    samples: tuple[MetricSample, ...] = ()

    @property
    def values(self) -> list[float]:
        return [s.value for s in self.samples]

    @property
    def latest(self) -> float | None:
        return self.samples[-1].value if self.samples else None

    def mean(self) -> float | None:
        return fmean(self.values) if self.samples else None

    def stdev(self) -> float | None:
        return pstdev(self.values) if len(self.samples) > 1 else None

    def percentile(self, p: float) -> float | None:
        if not self.samples:
            return None
        ordered = sorted(self.values)
        k = max(0, min(len(ordered) - 1, round((p / 100.0) * (len(ordered) - 1))))
        return ordered[k]

    def window(self, start: datetime, end: datetime) -> MetricSeries:
        return self.model_copy(
            update={"samples": tuple(s for s in self.samples if start <= s.at <= end)}
        )


class LogEntry(ValueObject):
    at: datetime
    service: str
    level: str
    message: str
    attributes: dict[str, str] = Field(default_factory=dict)


class TraceSpanSummary(ValueObject):
    service: str
    operation: str
    duration_ms: float
    error: bool = False
    depth: int = 0


class TraceSummary(ValueObject):
    trace_id: str
    at: datetime
    root_service: str
    duration_ms: float
    error: bool
    spans: tuple[TraceSpanSummary, ...] = ()

    @property
    def slowest_span(self) -> TraceSpanSummary | None:
        return max(self.spans, key=lambda s: s.duration_ms, default=None)


class ServiceHealth(ValueObject):
    service: str
    state: HealthState
    checks: dict[str, bool] = Field(default_factory=dict)
    message: str = ""
    at: datetime


class Deployment(ValueObject):
    service: str
    version: str
    deployed_at: datetime
    previous_version: str | None = None
    change_summary: str = ""
    rollback_available: bool = True


class ServiceNode(ValueObject):
    name: str
    kind: str  # service | database | cache | gateway
    tier: int = 0
    owner: str = ""
    replicas: int = 1
    version: str = ""


class DependencyEdge(ValueObject):
    source: str
    target: str
    protocol: str = "http"
    critical: bool = True


class Topology(ValueObject):
    nodes: tuple[ServiceNode, ...] = ()
    edges: tuple[DependencyEdge, ...] = ()

    def dependencies_of(self, service: str) -> list[str]:
        return [e.target for e in self.edges if e.source == service]

    def dependents_of(self, service: str) -> list[str]:
        return [e.source for e in self.edges if e.target == service]

    def downstream_closure(self, service: str) -> list[str]:
        """All transitive dependencies of ``service`` (what it relies on)."""
        seen: list[str] = []
        stack = [service]
        while stack:
            current = stack.pop()
            for dep in self.dependencies_of(current):
                if dep not in seen and dep != service:
                    seen.append(dep)
                    stack.append(dep)
        return seen

    def upstream_closure(self, service: str) -> list[str]:
        """All transitive dependents of ``service`` (what relies on it)."""
        seen: list[str] = []
        stack = [service]
        while stack:
            current = stack.pop()
            for dep in self.dependents_of(current):
                if dep not in seen and dep != service:
                    seen.append(dep)
                    stack.append(dep)
        return seen

    def has_node(self, service: str) -> bool:
        return any(n.name == service for n in self.nodes)

    def path_exists(self, source: str, target: str) -> bool:
        return target in self.downstream_closure(source)


class ResourceInfo(ValueObject):
    """Point-in-time introspection of an infrastructure component (database, cache…)."""

    component: str
    kind: str
    at: datetime
    metrics: dict[str, float] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)
    top_clients: dict[str, float] = Field(default_factory=dict)
