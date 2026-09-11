from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from aegis.domain.action import (
    ActionPlan,
    VerificationCondition,
    VerificationSpec,
    compute_idempotency_key,
)
from aegis.domain.enums import RiskLevel, Severity, SignalKind
from aegis.domain.flow import (
    BudgetUsage,
    ExecutionBudget,
    ExitCondition,
    FlowPack,
    FlowPhase,
    PhaseTransition,
)
from aegis.domain.ids import IncidentId
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.telemetry import DependencyEdge, MetricSample, MetricSeries, ServiceNode, Topology


def test_severity_ordering() -> None:
    assert Severity.SEV1.is_at_least(Severity.SEV3)
    assert not Severity.SEV4.is_at_least(Severity.SEV2)
    assert RiskLevel.HIGH.exceeds(RiskLevel.MEDIUM)
    assert not RiskLevel.LOW.exceeds(RiskLevel.LOW)


def test_budget_usage_exceeded_reports_each_dimension() -> None:
    budget = ExecutionBudget(
        max_iterations=2,
        max_tool_calls=3,
        max_llm_calls=1,
        max_llm_tokens=100,
        max_runtime_seconds=10,
    )
    usage = BudgetUsage(iterations=2, tool_calls=1, llm_calls=1, llm_tokens=50, runtime_seconds=3)
    reasons = usage.exceeded(budget)
    assert any("iterations" in r for r in reasons)
    assert any("llm_calls" in r for r in reasons)
    assert not any("tool_calls" in r for r in reasons)
    assert usage.add(tool_calls=2).tool_calls == 3


def test_budget_scaling_by_severity() -> None:
    budget = ExecutionBudget(max_iterations=20)
    assert budget.scaled(1.5).max_iterations == 30
    assert budget.scaled(0.75).max_iterations == 15


def _phase(
    name: str,
    *,
    tools: set[str] | None = None,
    terminal: bool = False,
    transitions: list[tuple[str, str]] | None = None,
) -> FlowPhase:
    return FlowPhase(
        name=name,
        objective=name,
        allowed_tools=frozenset(tools or set()),
        terminal=terminal,
        transitions=tuple(PhaseTransition(on=o, to=t) for o, t in (transitions or [])),  # type: ignore[arg-type]
        exit_conditions=(ExitCondition(kind="min_evidence", value=1),),
    )


def test_flowpack_validation_rejects_dangling_transition() -> None:
    with pytest.raises(ValueError, match="unknown"):
        FlowPack(
            name="f",
            version="1",
            initial_phase="a",
            phases=(
                _phase("a", tools={"get_metrics"}, transitions=[("exit_conditions_met", "zzz")]),
                _phase("done", terminal=True),
            ),
        )


def test_flowpack_requires_terminal_and_transitions() -> None:
    with pytest.raises(ValueError, match="no transitions"):
        FlowPack(
            name="f",
            version="1",
            initial_phase="a",
            phases=(_phase("a", tools={"x"}), _phase("done", terminal=True)),
        )
    with pytest.raises(ValueError, match="terminal"):
        FlowPack(
            name="f",
            version="1",
            initial_phase="a",
            phases=(_phase("a", tools={"x"}, transitions=[("exhausted", "a")]),),
        )


def test_flowpack_all_tools_and_budget() -> None:
    pack = FlowPack(
        name="f",
        version="1",
        initial_phase="a",
        phases=(
            _phase("a", tools={"x", "y"}, transitions=[("exit_conditions_met", "b")]),
            _phase("b", tools={"z"}, transitions=[("exhausted", "done")]),
            _phase("done", terminal=True),
        ),
        applies_to=frozenset({SignalKind.LATENCY}),
    )
    assert pack.all_tools == frozenset({"x", "y", "z"})
    assert pack.phase("b").next_phase("exhausted") == "done"
    assert pack.phase("b").next_phase("escalate") is None
    assert (
        pack.budget_for(Severity.SEV1).max_iterations
        > pack.budget_for(Severity.SEV4).max_iterations
    )
    assert pack.ref == "f@1"


def test_verification_condition_relative_and_absolute() -> None:
    rel = VerificationCondition(metric="p95", service="gw", max_ratio_to_baseline=1.5)
    ok, detail = rel.evaluate(observed=140, baseline=100)
    assert ok and "1.5" in detail
    assert rel.evaluate(observed=160, baseline=100)[0] is False
    assert rel.evaluate(observed=10, baseline=None) == (False, "no baseline available")
    absolute = VerificationCondition(metric="err", service="gw", comparator="lt", target=0.02)
    assert absolute.evaluate(0.01, None)[0]
    assert not absolute.evaluate(0.05, None)[0]


def test_action_plan_idempotency_key_is_stable_and_attempt_scoped() -> None:
    iid = IncidentId(uuid.uuid4())
    spec = VerificationSpec(conditions=(VerificationCondition(metric="m", service="s", target=1),))
    a = ActionPlan(
        incident_id=iid,
        tool_name="restart_service",
        arguments={"service": "x"},
        reason="r",
        expected_effect="e",
        verification=spec,
    )
    b = ActionPlan(
        incident_id=iid,
        tool_name="restart_service",
        arguments={"service": "x"},
        reason="r2",
        expected_effect="e2",
        verification=spec,
    )
    assert a.idempotency_key == b.idempotency_key
    c = ActionPlan(
        incident_id=iid,
        tool_name="restart_service",
        arguments={"service": "x"},
        reason="r",
        expected_effect="e",
        verification=spec,
        attempt=2,
    )
    assert c.idempotency_key != a.idempotency_key
    assert compute_idempotency_key(iid, "t", {"b": 1, "a": 2}) == compute_idempotency_key(
        iid, "t", {"a": 2, "b": 1}
    )


def test_metric_series_statistics() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    series = MetricSeries(
        service="s",
        metric="m",
        samples=tuple(
            MetricSample(at=t0 + timedelta(seconds=i), value=float(v))
            for i, v in enumerate([1, 2, 3, 4, 100])
        ),
    )
    assert series.latest == 100
    assert series.percentile(50) == 3
    assert series.percentile(100) == 100
    assert series.window(t0, t0 + timedelta(seconds=2)).values == [1, 2, 3]
    assert MetricSeries(service="s", metric="m").mean() is None


def test_topology_closures() -> None:
    topo = Topology(
        nodes=tuple(ServiceNode(name=n, kind="service") for n in ["gw", "order", "redis", "pg"]),
        edges=(
            DependencyEdge(source="gw", target="order"),
            DependencyEdge(source="order", target="redis"),
            DependencyEdge(source="order", target="pg"),
        ),
    )
    assert set(topo.downstream_closure("gw")) == {"order", "redis", "pg"}
    assert topo.upstream_closure("redis") == ["order", "gw"]
    assert topo.path_exists("gw", "redis")
    assert not topo.path_exists("redis", "gw")


def test_incident_attach_signal_dedupes() -> None:
    inc = Incident(title="t", severity=Severity.SEV3)
    sig = AnomalySignal(
        service="redis",
        metric="connections",
        kind=SignalKind.SATURATION,
        observed_value=900,
        baseline_value=100,
        deviation_sigma=8,
        detector="z",
        detected_at=datetime.now(tz=UTC),
        window_seconds=60,
    )
    inc.attach_signal(sig)
    inc.attach_signal(sig)
    assert len(inc.signals) == 1
    assert inc.affected_services == ["redis"]
    assert sig.magnitude == 8.0
