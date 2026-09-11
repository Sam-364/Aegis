"""Static description of the simulated world."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ComponentKind(StrEnum):
    GATEWAY = "gateway"
    SERVICE = "service"
    DATABASE = "database"
    CACHE = "cache"


class DependencyCall(BaseModel):
    target: str
    calls_per_request: float = 1.0
    critical: bool = True
    timeout_ms: float = 2000.0
    sensitivity: float = 1.0  # how much of the dependency's error rate propagates


class ServiceSpec(BaseModel):
    name: str
    kind: ComponentKind = ComponentKind.SERVICE
    tier: int = 1
    owner: str = "platform"
    base_latency_ms: float = 20.0
    capacity_rps_per_replica: float = 120.0
    replicas: int = 2
    version: str = "1.0.0"
    previous_version: str | None = None
    restart_seconds: float = 10.0
    scale_seconds: float = 20.0
    db_pool_size: int = 0  # per replica
    redis_pool_size: int = 0  # per replica
    memory_limit_mb: float = 1024.0
    deps: list[DependencyCall] = Field(default_factory=list)

    def dep(self, target: str) -> DependencyCall | None:
        return next((d for d in self.deps if d.target == target), None)


class InfraSpec(BaseModel):
    name: str
    kind: ComponentKind
    max_connections: int
    base_latency_ms: float
    restart_seconds: float = 8.0
    version: str = "16.4"


class WorldSpec(BaseModel):
    entry_rps: float = 120.0
    services: list[ServiceSpec]
    infra: list[InfraSpec]

    def service(self, name: str) -> ServiceSpec:
        for s in self.services:
            if s.name == name:
                return s
        raise KeyError(name)

    def infra_component(self, name: str) -> InfraSpec:
        for i in self.infra:
            if i.name == name:
                return i
        raise KeyError(name)

    def is_infra(self, name: str) -> bool:
        return any(i.name == name for i in self.infra)

    def names(self) -> list[str]:
        return [s.name for s in self.services] + [i.name for i in self.infra]

    def dependents_of(self, target: str) -> list[ServiceSpec]:
        return [s for s in self.services if s.dep(target) is not None]

    def topological_order(self) -> list[ServiceSpec]:
        """Leaves first: a service appears after every service it depends on."""
        ordered: list[ServiceSpec] = []
        visited: set[str] = set()

        def visit(spec: ServiceSpec) -> None:
            if spec.name in visited:
                return
            visited.add(spec.name)
            for d in spec.deps:
                if not self.is_infra(d.target):
                    visit(self.service(d.target))
            ordered.append(spec)

        for spec in self.services:
            visit(spec)
        return ordered


def default_world() -> WorldSpec:
    pg = "postgres"
    rd = "redis"
    return WorldSpec(
        entry_rps=120.0,
        services=[
            ServiceSpec(
                name="api-gateway",
                kind=ComponentKind.GATEWAY,
                tier=0,
                owner="edge",
                base_latency_ms=8.0,
                capacity_rps_per_replica=200.0,
                replicas=3,
                version="3.2.1",
                previous_version="3.2.0",
                deps=[
                    DependencyCall(target="auth-service", calls_per_request=1.0, timeout_ms=1500),
                    DependencyCall(target="user-service", calls_per_request=0.6),
                    DependencyCall(target="order-service", calls_per_request=0.5, timeout_ms=3000),
                    DependencyCall(
                        target="payment-service", calls_per_request=0.2, timeout_ms=3000
                    ),
                    DependencyCall(target="inventory-service", calls_per_request=0.3),
                    DependencyCall(
                        target="notification-service",
                        calls_per_request=0.1,
                        critical=False,
                        sensitivity=0.05,
                    ),
                ],
            ),
            ServiceSpec(
                name="auth-service",
                tier=1,
                owner="identity",
                base_latency_ms=12.0,
                capacity_rps_per_replica=150.0,
                replicas=2,
                version="2.8.0",
                previous_version="2.7.3",
                db_pool_size=5,
                redis_pool_size=10,
                deps=[
                    DependencyCall(target=rd, calls_per_request=1.0, timeout_ms=500),
                    DependencyCall(target=pg, calls_per_request=0.2, timeout_ms=1500),
                ],
            ),
            ServiceSpec(
                name="user-service",
                tier=1,
                owner="accounts",
                base_latency_ms=18.0,
                capacity_rps_per_replica=100.0,
                replicas=2,
                version="1.14.2",
                previous_version="1.14.1",
                db_pool_size=10,
                redis_pool_size=5,
                deps=[
                    DependencyCall(target=pg, calls_per_request=1.0, timeout_ms=1500),
                    DependencyCall(target=rd, calls_per_request=0.5, timeout_ms=500),
                ],
            ),
            ServiceSpec(
                name="order-service",
                tier=1,
                owner="commerce",
                base_latency_ms=20.0,
                capacity_rps_per_replica=80.0,
                replicas=3,
                version="4.1.0",
                previous_version="4.0.9",
                db_pool_size=8,
                redis_pool_size=12,
                deps=[
                    DependencyCall(target=pg, calls_per_request=1.0, timeout_ms=1500),
                    DependencyCall(target=rd, calls_per_request=1.5, timeout_ms=500),
                    DependencyCall(
                        target="inventory-service", calls_per_request=1.0, timeout_ms=2000
                    ),
                    DependencyCall(
                        target="payment-service", calls_per_request=0.4, timeout_ms=2500
                    ),
                ],
            ),
            ServiceSpec(
                name="payment-service",
                tier=2,
                owner="payments",
                base_latency_ms=35.0,
                capacity_rps_per_replica=60.0,
                replicas=2,
                version="2.3.7",
                previous_version="2.3.6",
                db_pool_size=6,
                redis_pool_size=4,
                memory_limit_mb=768.0,
                deps=[
                    DependencyCall(target=pg, calls_per_request=1.0, timeout_ms=1500),
                    DependencyCall(target=rd, calls_per_request=0.3, timeout_ms=500),
                ],
            ),
            ServiceSpec(
                name="inventory-service",
                tier=2,
                owner="commerce",
                base_latency_ms=15.0,
                capacity_rps_per_replica=120.0,
                replicas=2,
                version="1.9.3",
                previous_version="1.9.2",
                db_pool_size=6,
                redis_pool_size=6,
                deps=[
                    DependencyCall(target=pg, calls_per_request=1.0, timeout_ms=1500),
                    DependencyCall(target=rd, calls_per_request=0.8, timeout_ms=500),
                ],
            ),
            ServiceSpec(
                name="notification-service",
                tier=2,
                owner="growth",
                base_latency_ms=10.0,
                capacity_rps_per_replica=40.0,
                replicas=1,
                version="0.9.1",
                previous_version="0.9.0",
                redis_pool_size=4,
                deps=[DependencyCall(target=rd, calls_per_request=1.0, timeout_ms=500)],
            ),
        ],
        infra=[
            InfraSpec(
                name=pg,
                kind=ComponentKind.DATABASE,
                max_connections=100,
                base_latency_ms=4.0,
                version="16.4",
            ),
            InfraSpec(
                name=rd,
                kind=ComponentKind.CACHE,
                max_connections=250,
                base_latency_ms=0.8,
                version="7.2",
            ),
        ],
    )


SERVICE_METRICS: tuple[str, ...] = (
    "request_rate",
    "error_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "cpu_percent",
    "memory_percent",
    "db_pool_in_use",
    "db_pool_wait_ms",
    "redis_pool_in_use",
    "redis_pool_wait_ms",
    "replicas_ready",
    "up",
)
INFRA_METRICS: tuple[str, ...] = (
    "connections",
    "max_connections",
    "saturation",
    "ops_per_sec",
    "latency_p95_ms",
    "memory_percent",
    "blocked_clients",
    "idle_in_transaction",
    "lock_waits",
    "hit_rate",
    "up",
)
