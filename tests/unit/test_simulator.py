from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aegis.simulator.api import create_app
from aegis.simulator.engine import SimulationEngine, SimulationError
from aegis.simulator.faults import SCENARIOS, RemediationRef, clears_fault


@pytest.fixture
def engine() -> SimulationEngine:
    e = SimulationEngine(seed=7)
    e.warmup(240)
    return e


def apply(engine: SimulationEngine, ref: RemediationRef) -> None:
    match ref.action:
        case "restart":
            engine.restart(ref.target)
        case "rollback":
            engine.rollback(ref.target)
        case "scale":
            engine.scale(ref.target, replicas=int(ref.extra.get("replicas", 4)))
        case "rotate_pool":
            engine.rotate_pool(ref.target, target=str(ref.extra["target"]))
        case "clear_cache":
            engine.clear_cache(ref.target)
        case "none":
            return
        case other:
            raise AssertionError(other)


def test_determinism_same_seed_same_trajectory() -> None:
    a, b = SimulationEngine(seed=3, start_time=1000.0), SimulationEngine(seed=3, start_time=1000.0)
    for e in (a, b):
        e.warmup(60)
        e.inject("redis-connection-leak")
        e.advance(60)
    assert a.services["api-gateway"].latency_p95_ms == b.services["api-gateway"].latency_p95_ms
    assert a.infra["redis"].connections == b.infra["redis"].connections


def test_healthy_baseline(engine: SimulationEngine) -> None:
    gw = engine.services["api-gateway"]
    assert gw.error_rate < 0.02
    assert 100 < gw.latency_p95_ms < 400
    assert all(engine.health(n)["state"] == "healthy" for n in engine.services)
    assert engine.baseline("api-gateway", "latency_p95_ms") is not None
    assert engine.baseline("redis", "connections") is not None


@pytest.mark.parametrize(
    "scenario_id",
    [
        s
        for s in SCENARIOS
        if not SCENARIOS[s].expect_no_action and not SCENARIOS[s].expect_escalation
    ],
)
def test_correct_remediation_recovers_and_incorrect_does_not(
    engine: SimulationEngine, scenario_id: str
) -> None:
    spec = SCENARIOS[scenario_id]
    base_p95 = engine.baseline("api-gateway", "latency_p95_ms") or 0
    base_err = engine.baseline("api-gateway", "error_rate") or 0
    engine.inject(scenario_id)
    engine.advance(110)
    hint_metrics = spec.detection_hint_metrics
    root = engine.services.get(spec.root_cause_service)
    degraded = (
        engine.services["api-gateway"].latency_p95_ms > 1.5 * base_p95
        or engine.services["api-gateway"].error_rate > base_err + 0.03
        or (
            root is not None
            and (
                root.cpu_percent > 90
                or root.memory_percent > 60
                or root.latency_p95_ms > 2.5 * (engine.baseline(root.name, "latency_p95_ms") or 1)
            )
        )
    )
    assert degraded, f"{scenario_id} did not produce symptoms ({hint_metrics})"

    for wrong in spec.incorrect_remediations:
        apply(engine, wrong)
        engine.advance(45)
        assert engine.active_faults(), (
            f"{scenario_id}: incorrect remediation {wrong} cleared the fault"
        )

    apply(engine, spec.correct_remediations[0])
    engine.advance(60)
    assert not engine.active_faults(), f"{scenario_id}: correct remediation did not clear the fault"
    gw = engine.services["api-gateway"]
    assert gw.latency_p95_ms < 1.5 * base_p95 + 50, f"{scenario_id} latency did not recover"
    assert gw.error_rate < base_err + 0.03, f"{scenario_id} errors did not recover"


def test_transient_spike_self_resolves(engine: SimulationEngine) -> None:
    base_p95 = engine.baseline("api-gateway", "latency_p95_ms") or 0
    engine.inject("transient-spike")
    engine.advance(20)
    assert engine.services["api-gateway"].request_rate > 2.5 * 120
    assert engine.services["api-gateway"].latency_p95_ms > 1.3 * base_p95
    engine.advance(60)
    assert not engine.active_faults()
    assert engine.services["api-gateway"].latency_p95_ms < 1.3 * base_p95


def test_network_latency_has_no_remediation(engine: SimulationEngine) -> None:
    engine.inject("network-latency")
    engine.advance(20)
    diag = engine.diagnostic("connectivity", "payment-service", {"source": "api-gateway"})
    assert diag["added_network_latency_ms"] == 800.0
    engine.restart("payment-service")
    engine.advance(40)
    assert engine.active_faults()


def test_cascade_identifies_only_the_chain(engine: SimulationEngine) -> None:
    engine.inject("cascading-dependency")
    engine.advance(15)
    states = {n: engine.health(n)["state"] for n in engine.services}
    assert states["inventory-service"] == "unhealthy"
    assert states["order-service"] == "unhealthy"
    assert states["api-gateway"] in ("degraded", "unhealthy")
    assert states["auth-service"] == "healthy"
    assert states["payment-service"] == "healthy"
    deps = engine.diagnostic("dependencies", "order-service")
    assert deps["dependencies"][0]["target"] == "inventory-service"
    assert deps["dependencies"][0]["available"] is False


def test_redis_leak_is_attributable(engine: SimulationEngine) -> None:
    engine.inject("redis-connection-leak")
    engine.advance(95)
    cache = engine.diagnostic("cache", "redis")
    assert cache["top_client"] == "order-service"
    assert (
        cache["clients_by_service"]["order-service"]
        > 5 * cache["clients_by_service"]["auth-service"]
    )
    proc = engine.diagnostic("process", "order-service")
    assert proc["open_redis_connections"] > 100
    logs = engine.logs_for("redis", start=None, end=None, level="WARN", limit=10)
    assert logs, "redis should warn about client pressure"


def test_db_exhaustion_attributable_to_user_service(engine: SimulationEngine) -> None:
    engine.inject("db-pool-exhaustion")
    engine.advance(70)
    db = engine.diagnostic("database", "postgres")
    assert db["top_client"] == "user-service"
    assert db["idle_in_transaction_by_client"] == {
        "user-service": pytest.approx(db["idle_in_transaction"], abs=2)
    }


def test_bad_deployment_visible_in_deployments(engine: SimulationEngine) -> None:
    engine.inject("bad-deployment")
    engine.advance(5)
    deps = engine.deployments_for("payment-service")
    assert deps[-1].version == "2.4.0" and deps[-1].previous_version == "2.3.7"
    assert engine.services["payment-service"].version == "2.4.0"
    result = engine.rollback("payment-service")
    assert result["to_version"] == "2.3.7"
    engine.advance(20)
    assert engine.deployments_for("payment-service")[-1].kind == "rollback"


def test_clears_fault_ground_truth_table() -> None:
    from aegis.simulator.faults import new_fault

    f = new_fault(SCENARIOS["redis-connection-leak"], {}, 0.0)
    assert clears_fault(f, "restart", "order-service", {})
    assert clears_fault(f, "rotate_pool", "order-service", {"target": "redis"})
    assert not clears_fault(f, "rotate_pool", "order-service", {"target": "postgres"})
    assert not clears_fault(f, "restart", "redis", {})
    f2 = new_fault(SCENARIOS["cpu-saturation"], {}, 0.0)
    assert not clears_fault(f2, "scale", "notification-service", {"replicas": 1})
    assert not clears_fault(f2, "restart", "notification-service", {})
    assert clears_fault(f2, "scale", "notification-service", {"replicas": 2})
    assert clears_fault(f2, "scale", "notification-service", {"replicas": 3})


def test_errors_for_invalid_operations(engine: SimulationEngine) -> None:
    with pytest.raises(SimulationError):
        engine.inject("does-not-exist")
    with pytest.raises(SimulationError):
        engine.restart("nope")
    with pytest.raises(SimulationError):
        engine.rotate_pool("notification-service", target="postgres")
    with pytest.raises(SimulationError):
        engine.scale("order-service", replicas=99)
    with pytest.raises(SimulationError):
        engine.clear_cache("postgres")


def test_traces_and_logs_reflect_state(engine: SimulationEngine) -> None:
    engine.inject("bad-deployment")
    engine.advance(30)
    traces = engine.traces_for("payment-service", start=None, end=None, errors_only=True, limit=10)
    assert traces and all(t.error for t in traces)
    assert any(sp.service == "payment-service" and sp.error for t in traces for sp in t.spans)
    logs = engine.logs_for("payment-service", start=None, end=None, level="ERROR", limit=20)
    assert any("CheckoutHandler" in log.message for log in logs)


def test_http_api_roundtrip() -> None:
    app = create_app(SimulationEngine(seed=1), realtime=False, warmup_seconds=60)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert "sim_service_error_rate" in client.get("/metrics").text
        topo = client.get("/api/topology").json()
        assert {n["name"] for n in topo["nodes"]} >= {"api-gateway", "redis", "postgres"}
        r = client.post("/api/faults", json={"scenario_id": "redis-connection-leak"})
        assert r.status_code == 201
        fault_id = r.json()["id"]
        client.post("/api/control/advance", json={"seconds": 100})
        m = client.get("/api/components/redis/metrics", params={"metric": "connections"}).json()
        assert m["samples"][-1]["value"] > 200
        diag = client.post("/api/diagnostics", json={"kind": "cache", "target": "redis"}).json()
        assert diag["top_client"] == "order-service"
        act = client.post(
            "/api/actions/restart", json={"target": "order-service", "reason": "t"}
        ).json()
        assert act["status"] == "restarting"
        assert client.get("/api/faults").json()["faults"] == []
        assert client.delete(f"/api/faults/{fault_id}").status_code == 400  # already cleared
        assert client.post("/api/actions/restart", json={"target": "zzz"}).status_code == 400
        logs = client.get("/api/logs", params={"component": "order-service", "limit": 5}).json()[
            "logs"
        ]
        assert len(logs) <= 5
        assert client.get("/api/scenarios").json()["scenarios"]
