from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from aegis.detection.baselines import RollingBaseline
from aegis.detection.correlation import IncidentCorrelator
from aegis.detection.detector import AnomalyDetector, DetectorConfig
from aegis.detection.engine import DetectionEngine
from aegis.domain.clock import ManualClock
from aegis.domain.enums import Severity, SignalKind
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.telemetry import DependencyEdge, ServiceNode, Topology
from aegis.infrastructure.simulator.inprocess import InProcessSimulatorTelemetry
from aegis.simulator.engine import SimulationEngine
from aegis.simulator.faults import SCENARIOS


class FakeSink:
    def __init__(self) -> None:
        self.incidents: list[Incident] = []

    async def open_incidents(self) -> list[Incident]:
        return [i for i in self.incidents if i.is_active]

    async def open_incident(
        self,
        *,
        title: str,
        summary: str,
        severity: Severity,
        signals: Sequence[AnomalySignal],
        affected_services: Sequence[str],
        correlation_key: str,
    ) -> Incident:
        inc = Incident(
            title=title,
            summary=summary,
            severity=severity,
            signals=list(signals),
            affected_services=list(affected_services),
            correlation_key=correlation_key,
            detected_at=signals[0].detected_at,
            number=len(self.incidents) + 1,
        )
        self.incidents.append(inc)
        return inc

    async def attach_signals(
        self, incident_id: uuid.UUID, signals: Sequence[AnomalySignal]
    ) -> Incident:
        inc = next(i for i in self.incidents if i.id == incident_id)
        for s in signals:
            inc.attach_signal(s)
        return inc


class SimClock:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    def now(self) -> datetime:
        return self.engine.now


async def run_detection(
    engine: SimulationEngine, seconds: int, det: DetectionEngine, interval: int = 5
) -> None:
    for _ in range(seconds // interval):
        engine.advance(interval)
        await det.cycle()


@pytest.fixture
def world() -> tuple[SimulationEngine, DetectionEngine, FakeSink]:
    engine = SimulationEngine(seed=11)
    engine.warmup(600)
    sink = FakeSink()
    det = DetectionEngine(
        InProcessSimulatorTelemetry(engine), sink, interval_seconds=5, clock=SimClock(engine)
    )
    return engine, det, sink


def test_rolling_baseline_zscore_and_freeze() -> None:
    b = RollingBaseline(alpha=0.1, warmup=5)
    for v in [10, 10.2, 9.8, 10.1, 9.9, 10.0]:
        b.update(v)
    assert b.warm
    assert b.zscore(10.0) < 1
    assert b.zscore(30.0) > 3
    b.frozen = True
    b.update(1000)
    assert b.mean is not None and b.mean < 11
    restored = RollingBaseline.from_dict(b.to_dict())
    assert restored.mean == b.mean and restored.count == b.count


def test_detector_requires_consecutive_samples() -> None:
    det = AnomalyDetector(DetectorConfig(alpha=0.1, warmup=5))
    det.component_kinds = {"gw": "gateway"}
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(20):
        det.observe("gw", "latency_p95_ms", 100 + (i % 3), t + timedelta(seconds=i))
    signals, _, _ = det.observe("gw", "latency_p95_ms", 900, t + timedelta(seconds=21))
    assert signals == []  # single spike is not enough
    signals, _, _ = det.observe("gw", "latency_p95_ms", 900, t + timedelta(seconds=22))
    assert signals == []
    signals, _, _ = det.observe("gw", "latency_p95_ms", 900, t + timedelta(seconds=23))
    assert len(signals) == 1
    sig = signals[0]
    assert (
        sig.kind is SignalKind.LATENCY and sig.deviation_sigma > 3 and 99 < sig.baseline_value < 103
    )
    # baseline is frozen while firing
    assert det.baseline_for("gw", "latency_p95_ms").frozen
    # no duplicate signal while still firing
    signals, _, _ = det.observe("gw", "latency_p95_ms", 950, t + timedelta(seconds=24))
    assert signals == []
    # clears after enough normal samples
    cleared_total = []
    for i in range(10):
        _, cleared, _ = det.observe("gw", "latency_p95_ms", 101, t + timedelta(seconds=30 + i))
        cleared_total += cleared
    assert cleared_total == ["latency_p95_ms:latency"]
    assert not det.baseline_for("gw", "latency_p95_ms").frozen


def test_flat_series_does_not_fire_on_tiny_change() -> None:
    det = AnomalyDetector(DetectorConfig(alpha=0.1, warmup=5))
    det.component_kinds = {"redis": "cache"}
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(20):
        det.observe("redis", "connections", 12.0, t + timedelta(seconds=i))
    for i in range(5):
        signals, _, _ = det.observe("redis", "connections", 13.0, t + timedelta(seconds=30 + i))
        assert signals == []  # z would be huge on a flat series but ratio/delta guards hold


def _topology() -> Topology:
    return Topology(
        nodes=(
            ServiceNode(name="api-gateway", kind="gateway", tier=0),
            ServiceNode(name="order-service", kind="service", tier=1),
            ServiceNode(name="notification-service", kind="service", tier=2),
            ServiceNode(name="redis", kind="cache", tier=3),
        ),
        edges=(
            DependencyEdge(source="api-gateway", target="order-service"),
            DependencyEdge(source="order-service", target="redis"),
            DependencyEdge(source="api-gateway", target="notification-service", critical=False),
        ),
    )


def _sig(
    service: str,
    metric: str,
    kind: SignalKind,
    value: float,
    base: float,
    at: datetime,
    sigma: float = 8.0,
) -> AnomalySignal:
    return AnomalySignal(
        service=service,
        metric=metric,
        kind=kind,
        observed_value=value,
        baseline_value=base,
        deviation_sigma=sigma,
        detector="t",
        detected_at=at,
        window_seconds=10,
        description=f"{service} {metric}",
    )


def test_correlator_groups_dependency_chain_into_one_incident() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    signals = [
        _sig("redis", "connections", SignalKind.SATURATION, 240, 12, t),
        _sig(
            "order-service",
            "redis_pool_wait_ms",
            SignalKind.SATURATION,
            500,
            0,
            t + timedelta(seconds=20),
        ),
        _sig(
            "api-gateway",
            "latency_p95_ms",
            SignalKind.LATENCY,
            2500,
            200,
            t + timedelta(seconds=30),
        ),
        _sig(
            "api-gateway",
            "error_rate",
            SignalKind.ERROR_RATE,
            0.6,
            0.005,
            t + timedelta(seconds=35),
        ),
    ]
    res = IncidentCorrelator().correlate(signals, [], _topology(), t + timedelta(seconds=40))
    assert len(res.opened) == 1
    opened = res.opened[0]
    assert opened.severity is Severity.SEV1
    assert opened.title == "Elevated API error rate"
    assert opened.affected_services == ["api-gateway", "order-service", "redis"]


def test_correlator_attaches_to_open_related_incident() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    existing = Incident(
        title="x", severity=Severity.SEV2, affected_services=["redis"], detected_at=t
    )
    later = [
        _sig(
            "api-gateway", "latency_p95_ms", SignalKind.LATENCY, 900, 200, t + timedelta(seconds=90)
        )
    ]
    res = IncidentCorrelator().correlate(later, [existing], _topology(), t + timedelta(seconds=95))
    assert res.opened == [] and len(res.attached) == 1
    assert res.attached[0].incident is existing


def test_correlator_severity_ladder() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    c = IncidentCorrelator()
    topo = _topology()
    assert (
        c.severity_for([_sig("api-gateway", "up", SignalKind.AVAILABILITY, 0, 1, t)], topo)
        is Severity.SEV1
    )
    assert (
        c.severity_for(
            [_sig("api-gateway", "latency_p95_ms", SignalKind.LATENCY, 700, 200, t)], topo
        )
        is Severity.SEV2
    )
    assert (
        c.severity_for(
            [_sig("notification-service", "cpu_percent", SignalKind.RESOURCE, 95, 20, t)], topo
        )
        is Severity.SEV4
    )
    assert (
        c.severity_for([_sig("notification-service", "up", SignalKind.AVAILABILITY, 0, 1, t)], topo)
        is Severity.SEV3
    )
    assert (
        c.title_for(
            [_sig("notification-service", "cpu_percent", SignalKind.RESOURCE, 95, 20, t)], topo
        )
        == "notification-service CPU pressure"
    )


async def test_no_false_positives_on_clean_run(
    world: tuple[SimulationEngine, DetectionEngine, FakeSink],
) -> None:
    engine, det, sink = world
    await det.bootstrap()
    await run_detection(engine, 600, det)
    assert sink.incidents == [], [i.title for i in sink.incidents]


@pytest.mark.parametrize(
    "scenario_id,expect_services,max_detect_seconds",
    [
        ("redis-connection-leak", {"redis"}, 60),
        ("bad-deployment", {"payment-service"}, 30),
        ("db-pool-exhaustion", {"postgres"}, 60),
        ("cascading-dependency", {"inventory-service"}, 20),
        ("transient-spike", {"api-gateway"}, 20),
        ("cpu-saturation", {"notification-service"}, 30),
    ],
)
async def test_scenarios_produce_exactly_one_incident(
    world: tuple[SimulationEngine, DetectionEngine, FakeSink],
    scenario_id: str,
    expect_services: set[str],
    max_detect_seconds: int,
) -> None:
    engine, det, sink = world
    await det.bootstrap()
    await run_detection(engine, 60, det)
    assert sink.incidents == []
    engine.inject(scenario_id)
    injected_at = engine.now
    first_detection: datetime | None = None
    for _ in range(36):  # 3 minutes
        engine.advance(5)
        await det.cycle()
        if sink.incidents and first_detection is None:
            first_detection = sink.incidents[0].detected_at
    assert len(sink.incidents) == 1, [(i.title, i.affected_services) for i in sink.incidents]
    inc = sink.incidents[0]
    assert first_detection is not None
    assert (first_detection - injected_at).total_seconds() <= max_detect_seconds
    assert expect_services <= set(inc.affected_services), inc.affected_services
    spec = SCENARIOS[scenario_id]
    if spec.root_cause_service != "api-gateway":
        assert (
            spec.root_cause_service in inc.affected_services or scenario_id == "db-pool-exhaustion"
        )
    assert inc.severity in (Severity.SEV1, Severity.SEV2, Severity.SEV3, Severity.SEV4)


async def test_bootstrap_from_history_makes_detection_immediate() -> None:
    engine = SimulationEngine(seed=5)
    engine.warmup(600)
    sink = FakeSink()
    det = DetectionEngine(
        InProcessSimulatorTelemetry(engine), sink, interval_seconds=5, clock=SimClock(engine)
    )
    fed = await det.bootstrap()
    assert fed > 1000
    assert all(b.warm for b in det.detector.baselines.values())
    engine.inject("cascading-dependency")
    await run_detection(engine, 30, det)
    assert len(sink.incidents) == 1


async def test_detector_state_roundtrip(
    world: tuple[SimulationEngine, DetectionEngine, FakeSink],
) -> None:
    _engine, det, _ = world
    await det.bootstrap()
    exported = det.detector.export()
    fresh = AnomalyDetector()
    fresh.load(exported)
    assert len(fresh.baselines) == len(det.detector.baselines)
    assert fresh.component_kinds["redis"] == "cache"
    _ = ManualClock()


async def test_persisted_baselines_are_a_prior_not_the_truth() -> None:
    """A saved baseline can describe a world that no longer exists. Replaying recent history over
    it must prevent the first live sample from firing a false positive."""
    from aegis.infrastructure.memory.messaging import InMemoryBaselineStore

    engine = SimulationEngine(seed=42)
    engine.warmup(600)
    store = InMemoryBaselineStore()
    first = DetectionEngine(
        InProcessSimulatorTelemetry(engine),
        FakeSink(),
        interval_seconds=5,
        baseline_store=store,
        clock=SimClock(engine),
    )
    await first.bootstrap()
    await store.save("detector", first.detector.export(), ttl_seconds=3600)
    saved_mean = store.data["detector"]["baselines"]["api-gateway|latency_p95_ms"]["mean"]

    # the observed world is rebuilt with different characteristics
    engine.reset(seed=7)
    engine.warmup(600)
    for spec in engine.world.services:
        spec.base_latency_ms *= 3
    engine.advance(600)
    current = engine.services["api-gateway"].latency_p95_ms
    assert current > saved_mean * 1.8, "the rebuilt world must differ from the saved baseline"

    sink = FakeSink()
    restarted = DetectionEngine(
        InProcessSimulatorTelemetry(engine),
        sink,
        interval_seconds=5,
        baseline_store=store,
        clock=SimClock(engine),
    )
    await restarted.bootstrap()
    adopted = restarted.detector.baseline_for("api-gateway", "latency_p95_ms").mean
    assert adopted is not None and adopted > saved_mean * 1.5, "history must override the prior"
    await run_detection(engine, 120, restarted)
    assert sink.incidents == [], [i.title for i in sink.incidents]
