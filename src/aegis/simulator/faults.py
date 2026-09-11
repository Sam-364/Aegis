"""Fault scenarios with ground truth.

Each scenario declares its symptoms, root cause, which remediation *actually* clears it and which
remediations look plausible but do not. The same catalog drives the simulator, the E2E harness
and the agent evals, so "did the agent get it right" has one definition.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class RemediationRef(BaseModel):
    """A remediation as (action, target[, extra])."""

    action: str  # restart | rollback | scale | rotate_pool | clear_cache | none
    target: str
    extra: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> tuple[str, str]:
        return (self.action, self.target)


class ScenarioSpec(BaseModel):
    id: str
    title: str
    description: str
    fault_type: str
    default_params: dict[str, Any] = Field(default_factory=dict)
    root_cause_service: str
    root_cause_category: str
    expected_symptoms: list[str]
    correct_remediations: list[RemediationRef]
    incorrect_remediations: list[RemediationRef] = Field(default_factory=list)
    expect_no_action: bool = False
    expect_escalation: bool = False
    self_resolving_seconds: float | None = None
    detection_hint_metrics: list[str] = Field(default_factory=list)


SCENARIOS: dict[str, ScenarioSpec] = {
    s.id: s
    for s in [
        ScenarioSpec(
            id="redis-connection-leak",
            title="Redis connection leak in order-service",
            description="order-service workers hold Redis connections without releasing them. "
            "Redis approaches max clients, order-service waits on its pool, API latency and "
            "5xx rise.",
            fault_type="redis_connection_leak",
            default_params={"service": "order-service", "rate_per_second": 2.5},
            root_cause_service="order-service",
            root_cause_category="resource_exhaustion",
            expected_symptoms=[
                "api-gateway latency_p95_ms",
                "redis connections",
                "order-service redis_pool_wait_ms",
                "api-gateway error_rate",
            ],
            correct_remediations=[
                RemediationRef(action="restart", target="order-service"),
                RemediationRef(
                    action="rotate_pool", target="order-service", extra={"target": "redis"}
                ),
            ],
            incorrect_remediations=[
                RemediationRef(action="restart", target="api-gateway"),
                RemediationRef(action="restart", target="redis"),
                RemediationRef(action="clear_cache", target="redis"),
                RemediationRef(action="scale", target="order-service"),
            ],
            detection_hint_metrics=["latency_p95_ms", "connections", "redis_pool_wait_ms"],
        ),
        ScenarioSpec(
            id="bad-deployment",
            title="Regression in payment-service 2.4.0",
            description="payment-service 2.4.0 ships a bug in the checkout handler: elevated 5xx "
            "and latency on payment calls, propagating to order-service and the gateway.",
            fault_type="bad_deployment",
            default_params={
                "service": "payment-service",
                "version": "2.4.0",
                "error_rate": 0.35,
                "added_latency_ms": 350.0,
            },
            root_cause_service="payment-service",
            root_cause_category="deployment_regression",
            expected_symptoms=[
                "payment-service error_rate",
                "api-gateway error_rate",
                "new deployment payment-service 2.4.0",
            ],
            correct_remediations=[RemediationRef(action="rollback", target="payment-service")],
            incorrect_remediations=[
                RemediationRef(action="restart", target="payment-service"),
                RemediationRef(action="restart", target="order-service"),
                RemediationRef(action="scale", target="payment-service"),
            ],
            detection_hint_metrics=["error_rate", "latency_p95_ms"],
        ),
        ScenarioSpec(
            id="db-pool-exhaustion",
            title="Postgres connection exhaustion from user-service",
            description="user-service leaves sessions idle-in-transaction. Postgres runs out of "
            "connection slots; every database-backed service waits on its pool.",
            fault_type="db_pool_exhaustion",
            default_params={"service": "user-service", "rate_per_second": 1.2},
            root_cause_service="user-service",
            root_cause_category="resource_exhaustion",
            expected_symptoms=[
                "postgres connections",
                "postgres idle_in_transaction",
                "user-service db_pool_wait_ms",
                "api-gateway latency_p95_ms",
            ],
            correct_remediations=[
                RemediationRef(
                    action="rotate_pool", target="user-service", extra={"target": "postgres"}
                ),
                RemediationRef(action="restart", target="user-service"),
            ],
            incorrect_remediations=[
                RemediationRef(action="scale", target="user-service"),
                RemediationRef(action="restart", target="postgres"),
                RemediationRef(action="restart", target="order-service"),
            ],
            detection_hint_metrics=["connections", "db_pool_wait_ms", "latency_p95_ms"],
        ),
        ScenarioSpec(
            id="cascading-dependency",
            title="inventory-service crash cascades to orders and the gateway",
            description="inventory-service is OOM-killed. order-service depends on it critically; "
            "order errors propagate to the gateway. The gateway is a symptom, not the cause.",
            fault_type="service_crash",
            default_params={"service": "inventory-service"},
            root_cause_service="inventory-service",
            root_cause_category="dependency_failure",
            expected_symptoms=[
                "inventory-service up",
                "order-service error_rate",
                "api-gateway error_rate",
            ],
            correct_remediations=[RemediationRef(action="restart", target="inventory-service")],
            incorrect_remediations=[
                RemediationRef(action="restart", target="api-gateway"),
                RemediationRef(action="restart", target="order-service"),
                RemediationRef(action="scale", target="order-service"),
            ],
            detection_hint_metrics=["up", "error_rate"],
        ),
        ScenarioSpec(
            id="transient-spike",
            title="Short traffic burst that self-recovers",
            description="A 45 second burst triples entry traffic. Latency rises briefly and "
            "recovers without intervention. The correct action is none.",
            fault_type="traffic_spike",
            default_params={"multiplier": 3.0, "duration_seconds": 45.0},
            root_cause_service="api-gateway",
            root_cause_category="transient",
            expected_symptoms=["api-gateway request_rate", "api-gateway latency_p95_ms"],
            correct_remediations=[RemediationRef(action="none", target="")],
            incorrect_remediations=[
                RemediationRef(action="restart", target="api-gateway"),
                RemediationRef(action="scale", target="api-gateway"),
            ],
            expect_no_action=True,
            self_resolving_seconds=45.0,
            detection_hint_metrics=["request_rate", "latency_p95_ms"],
        ),
        ScenarioSpec(
            id="memory-leak",
            title="Memory leak in payment-service",
            description="payment-service heap grows steadily; GC pauses inflate latency and the "
            "process eventually restarts on OOM.",
            fault_type="memory_leak",
            default_params={"service": "payment-service", "rate_mb_per_second": 4.0},
            root_cause_service="payment-service",
            root_cause_category="resource_exhaustion",
            expected_symptoms=["payment-service memory_percent", "payment-service latency_p95_ms"],
            correct_remediations=[RemediationRef(action="restart", target="payment-service")],
            incorrect_remediations=[
                RemediationRef(action="rollback", target="payment-service"),
                RemediationRef(action="restart", target="order-service"),
            ],
            detection_hint_metrics=["memory_percent", "latency_p95_ms"],
        ),
        ScenarioSpec(
            id="cpu-saturation",
            title="CPU saturation in notification-service",
            description="A burst of notification jobs pins the single notification-service "
            "replica. Its latency climbs; the gateway is barely affected because the call is "
            "non-critical.",
            fault_type="cpu_saturation",
            default_params={"service": "notification-service", "pressure": 75.0},
            root_cause_service="notification-service",
            root_cause_category="capacity",
            expected_symptoms=[
                "notification-service cpu_percent",
                "notification-service latency_p95_ms",
            ],
            correct_remediations=[
                RemediationRef(action="scale", target="notification-service", extra={"replicas": 2})
            ],
            incorrect_remediations=[
                RemediationRef(action="restart", target="notification-service"),
                RemediationRef(action="restart", target="api-gateway"),
            ],
            detection_hint_metrics=["cpu_percent", "latency_p95_ms"],
        ),
        ScenarioSpec(
            id="network-latency",
            title="Network latency between api-gateway and payment-service",
            description="An upstream network path adds 800ms to gateway→payment calls. No in-scope "
            "remediation fixes the network; the correct outcome is escalation.",
            fault_type="network_latency",
            default_params={
                "source": "api-gateway",
                "target": "payment-service",
                "added_ms": 800.0,
            },
            root_cause_service="payment-service",
            root_cause_category="network",
            expected_symptoms=["api-gateway latency_p95_ms"],
            correct_remediations=[RemediationRef(action="none", target="")],
            incorrect_remediations=[
                RemediationRef(action="restart", target="payment-service"),
                RemediationRef(action="rollback", target="payment-service"),
            ],
            expect_escalation=True,
            detection_hint_metrics=["latency_p95_ms"],
        ),
    ]
}

_fault_counter = itertools.count(1)


@dataclass
class ActiveFault:
    id: str
    scenario_id: str
    fault_type: str
    params: dict[str, Any]
    started_at: float
    duration_seconds: float | None = None
    cleared_at: float | None = None
    cleared_by: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.cleared_at is None

    def expired(self, now: float) -> bool:
        return self.duration_seconds is not None and now - self.started_at >= self.duration_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "fault_type": self.fault_type,
            "params": self.params,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "cleared_at": self.cleared_at,
            "cleared_by": self.cleared_by,
            "active": self.active,
        }


def new_fault(scenario: ScenarioSpec, params: dict[str, Any], now: float) -> ActiveFault:
    merged = {**scenario.default_params, **params}
    duration = merged.pop("duration_seconds", None)
    if scenario.self_resolving_seconds is not None and duration is None:
        duration = scenario.self_resolving_seconds
    return ActiveFault(
        id=f"fault-{next(_fault_counter)}",
        scenario_id=scenario.id,
        fault_type=scenario.fault_type,
        params=merged,
        started_at=now,
        duration_seconds=float(duration) if duration is not None else None,
    )


def clears_fault(  # noqa: PLR0911 - one return per fault type is the clearest table
    fault: ActiveFault, action: str, target: str, extra: dict[str, Any]
) -> bool:
    """Does a remediation actually fix the underlying fault? Ground truth lives here."""
    p = fault.params
    match fault.fault_type:
        case "redis_connection_leak":
            svc = p["service"]
            return (action == "restart" and target == svc) or (
                action == "rotate_pool" and target == svc and extra.get("target") == "redis"
            )
        case "bad_deployment":
            return action == "rollback" and target == p["service"]
        case "db_pool_exhaustion":
            svc = p["service"]
            return (action == "restart" and target == svc) or (
                action == "rotate_pool" and target == svc and extra.get("target") == "postgres"
            )
        case "service_crash" | "memory_leak":
            return action == "restart" and target == p["service"]
        case "cpu_saturation":
            # Any scale-up resolves it: cpu_pressure is divided by the ready replica count, so at
            # two replicas CPU returns to ~55% and latency to baseline. A restart does not help,
            # because the same single replica meets the same burst.
            return (
                action == "scale" and target == p["service"] and int(extra.get("replicas", 0)) >= 2
            )
        case "traffic_spike" | "network_latency":
            return False
        case _:
            return False
