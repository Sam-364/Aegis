"""Mutable runtime state of the simulated world."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceState:
    name: str
    version: str
    replicas: int
    replicas_ready: float
    up: bool = True
    crashed: bool = False
    restarting_until: float | None = None
    scaling_until: float | None = None
    scale_from: float = 0.0
    rolling_until: float | None = None
    request_rate: float = 0.0
    error_rate: float = 0.0
    own_error_rate: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    own_latency_ms: float = 0.0
    cpu_percent: float = 15.0
    memory_percent: float = 35.0
    db_pool_in_use: float = 0.0
    db_pool_wait_ms: float = 0.0
    redis_pool_in_use: float = 0.0
    redis_pool_wait_ms: float = 0.0
    leaked_db: float = 0.0
    leaked_redis: float = 0.0
    intrinsic_error: float = 0.0
    intrinsic_latency_ms: float = 0.0
    cpu_pressure: float = 0.0
    memory_leak_mb: float = 0.0
    edge_latency_ms: dict[str, float] = field(default_factory=dict)
    restart_count: int = 0
    last_restart_at: float | None = None
    started_at: float = 0.0
    dep_error_detail: dict[str, float] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.up and not self.crashed and self.restarting_until is None

    def metrics(self) -> dict[str, float]:
        return {
            "request_rate": self.request_rate,
            "error_rate": self.error_rate,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "db_pool_in_use": self.db_pool_in_use,
            "db_pool_wait_ms": self.db_pool_wait_ms,
            "redis_pool_in_use": self.redis_pool_in_use,
            "redis_pool_wait_ms": self.redis_pool_wait_ms,
            "replicas_ready": self.replicas_ready,
            "up": 1.0 if self.available else 0.0,
        }


@dataclass
class InfraState:
    name: str
    kind: str
    version: str
    max_connections: int
    up: bool = True
    restarting_until: float | None = None
    connections: float = 0.0
    saturation: float = 0.0
    ops_per_sec: float = 0.0
    latency_p95_ms: float = 0.0
    memory_percent: float = 30.0
    blocked_clients: float = 0.0
    idle_in_transaction: float = 0.0
    lock_waits: float = 0.0
    hit_rate: float = 0.97
    cache_flushed_until: float | None = None
    connections_by_client: dict[str, float] = field(default_factory=dict)
    restart_count: int = 0

    @property
    def available(self) -> bool:
        return self.up and self.restarting_until is None

    def metrics(self) -> dict[str, float]:
        return {
            "connections": self.connections,
            "max_connections": float(self.max_connections),
            "saturation": self.saturation,
            "ops_per_sec": self.ops_per_sec,
            "latency_p95_ms": self.latency_p95_ms,
            "memory_percent": self.memory_percent,
            "blocked_clients": self.blocked_clients,
            "idle_in_transaction": self.idle_in_transaction,
            "lock_waits": self.lock_waits,
            "hit_rate": self.hit_rate,
            "up": 1.0 if self.available else 0.0,
        }


@dataclass
class DeploymentRecord:
    service: str
    version: str
    previous_version: str | None
    deployed_at: float
    change_summary: str
    kind: str = "deploy"  # deploy | rollback


@dataclass
class LogRecord:
    at: float
    service: str
    level: str
    message: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class SpanRecord:
    service: str
    operation: str
    duration_ms: float
    error: bool
    depth: int = 0


@dataclass
class TraceRecord:
    trace_id: str
    at: float
    root_service: str
    duration_ms: float
    error: bool
    spans: list[SpanRecord]
