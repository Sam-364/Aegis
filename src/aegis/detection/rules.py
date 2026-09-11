"""Declarative metric rules. A rule fires when its statistical and absolute guards agree."""

from __future__ import annotations

from dataclasses import dataclass

from aegis.domain.enums import SignalKind


@dataclass(frozen=True)
class MetricRule:
    metric: str
    kind: SignalKind
    description: str
    zscore_threshold: float | None = 3.0
    min_ratio: float | None = None  # observed / baseline
    min_delta: float | None = None  # observed - baseline
    absolute_min: float | None = None  # fires when observed >= absolute_min regardless of baseline
    absolute_max: float | None = None  # fires when observed <= absolute_max (availability)
    min_consecutive: int = 3
    clear_after: int = 6  # consecutive normal samples before the rule stops firing
    component_kinds: frozenset[str] = frozenset({"gateway", "service", "database", "cache"})

    @property
    def name(self) -> str:
        return f"{self.metric}:{self.kind.value}"

    def statistical_trigger(self, value: float, zscore: float, ratio: float, mean: float) -> bool:
        if self.zscore_threshold is None:
            return False
        if zscore < self.zscore_threshold:
            return False
        if self.min_ratio is not None and ratio < self.min_ratio:
            return False
        return not (self.min_delta is not None and (value - mean) < self.min_delta)

    def absolute_trigger(self, value: float) -> bool:
        if self.absolute_min is not None and value >= self.absolute_min:
            return True
        return self.absolute_max is not None and value <= self.absolute_max


DEFAULT_RULES: tuple[MetricRule, ...] = (
    MetricRule(
        "latency_p95_ms",
        SignalKind.LATENCY,
        "p95 latency regression",
        zscore_threshold=3.0,
        min_ratio=1.6,
        min_consecutive=3,
    ),
    MetricRule(
        "error_rate",
        SignalKind.ERROR_RATE,
        "elevated error rate",
        zscore_threshold=3.0,
        min_delta=0.03,
        absolute_min=0.25,
        min_consecutive=2,
    ),
    MetricRule(
        "up",
        SignalKind.AVAILABILITY,
        "component unavailable",
        zscore_threshold=None,
        absolute_max=0.5,
        min_consecutive=2,
        clear_after=3,
    ),
    MetricRule(
        "connections",
        SignalKind.SATURATION,
        "connection count growth",
        zscore_threshold=3.0,
        min_ratio=1.8,
        min_delta=15,
        min_consecutive=3,
        component_kinds=frozenset({"database", "cache"}),
    ),
    MetricRule(
        "saturation",
        SignalKind.SATURATION,
        "connection saturation",
        zscore_threshold=None,
        absolute_min=0.85,
        min_consecutive=2,
        component_kinds=frozenset({"database", "cache"}),
    ),
    MetricRule(
        "idle_in_transaction",
        SignalKind.SATURATION,
        "idle-in-transaction sessions",
        zscore_threshold=3.0,
        min_delta=10,
        min_consecutive=3,
        component_kinds=frozenset({"database"}),
    ),
    MetricRule(
        "cpu_percent",
        SignalKind.RESOURCE,
        "CPU saturation",
        zscore_threshold=None,
        absolute_min=90.0,
        min_consecutive=3,
    ),
    MetricRule(
        "memory_percent",
        SignalKind.RESOURCE,
        "memory pressure",
        zscore_threshold=3.0,
        min_ratio=1.3,
        absolute_min=85.0,
        min_consecutive=3,
    ),
    MetricRule(
        "request_rate",
        SignalKind.TRAFFIC,
        "traffic surge",
        zscore_threshold=3.0,
        min_ratio=2.0,
        min_consecutive=2,
        component_kinds=frozenset({"gateway"}),
    ),
    MetricRule(
        "db_pool_wait_ms",
        SignalKind.SATURATION,
        "database pool wait",
        zscore_threshold=None,
        absolute_min=200.0,
        min_consecutive=2,
    ),
    MetricRule(
        "redis_pool_wait_ms",
        SignalKind.SATURATION,
        "redis pool wait",
        zscore_threshold=None,
        absolute_min=200.0,
        min_consecutive=2,
    ),
)
