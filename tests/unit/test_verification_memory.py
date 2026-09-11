from __future__ import annotations

import uuid

import pytest

from aegis.domain.action import ActionPlan, VerificationCondition, VerificationSpec
from aegis.domain.enums import ActionPlanStatus, HypothesisCategory, Severity, VerificationStatus
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.memory import SimilarIncidentQuery
from aegis.llm.provider_scripted import HashEmbeddingProvider, ScriptedProvider
from aegis.memory.service import IncidentMemoryService
from aegis.remediation.planning import build_verification_spec, default_rollback
from aegis.verification.engine import VerificationEngine, describe_change
from tests.helpers import SimClock, build_runtime


class SimSleeper:
    """Advance simulated time instead of sleeping."""

    def __init__(self, engine) -> None:  # type: ignore[no-untyped-def]
        self.engine = engine
        self.slept = 0.0

    async def __call__(self, seconds: float) -> None:
        self.engine.advance(seconds)
        self.slept += seconds


async def test_verification_passes_after_correct_remediation() -> None:
    rt = build_runtime(seed=9, warmup=600)
    rt.engine.inject("redis-connection-leak")
    rt.engine.advance(120)
    inc = rt.incident(services=["api-gateway", "redis"])
    sleeper = SimSleeper(rt.engine)
    engine = VerificationEngine(
        rt.telemetry, clock=SimClock(rt.engine), sleep=sleeper, poll_seconds=5
    )
    baselines = {
        (s.service, s.metric): await rt.telemetry.baseline(s.service, s.metric) for s in inc.signals
    }
    baselines[("redis", "connections")] = await rt.telemetry.baseline("redis", "connections")
    spec = build_verification_spec(
        inc,
        baselines=baselines,
        target_service="order-service",
        tool_spec=rt.registry.spec("restart_service"),
        stabilization_seconds=15,
        timeout_seconds=180,
    )
    assert {(c.service, c.metric) for c in spec.conditions} >= {
        ("api-gateway", "latency_p95_ms"),
        ("order-service", "up"),
    }
    before = await engine.snapshot(spec)
    assert before["api-gateway.latency_p95_ms"] > 1000
    # wrong remediation: verification must fail
    rt.engine.restart("api-gateway")
    result = await engine.verify(spec, before=before, baselines=baselines)
    assert result.status is VerificationStatus.FAILED
    assert "still failing" in result.summary
    # right remediation: verification must pass and show recovery
    rt.engine.restart("order-service")
    result = await engine.verify(spec, before=before, baselines=baselines)
    assert result.status is VerificationStatus.PASSED, result.summary
    assert result.after["api-gateway.latency_p95_ms"] < 400
    changes = describe_change(result.before, result.after)
    assert any("api-gateway.latency_p95_ms" in c and "-" in c for c in changes)


async def test_verification_inconclusive_without_telemetry() -> None:
    class Dead:
        async def baseline(self, s: str, m: str) -> float | None:
            return None

        async def metrics(self, *a, **k):  # type: ignore[no-untyped-def]
            from aegis.domain.errors import InfrastructureError

            raise InfrastructureError("down")

    async def nosleep(_: float) -> None:
        return None

    spec = VerificationSpec(
        conditions=(VerificationCondition(metric="error_rate", service="x", target=0.05),),
        stabilization_seconds=0,
        timeout_seconds=1,
    )
    engine = VerificationEngine(Dead(), sleep=nosleep, poll_seconds=0.5)  # type: ignore[arg-type]
    result = await engine.verify(spec)
    assert result.status is VerificationStatus.INCONCLUSIVE


def test_default_rollbacks() -> None:
    assert default_rollback("restart_service", {"service": "x"}).available is False
    rb = default_rollback(
        "rollback_deployment", {"service": "x"}, previous={"from_version": "2.4.0"}
    )
    assert rb.available and rb.arguments["to_version"] == "2.4.0"
    rb = default_rollback("scale_service", {"service": "x"}, previous={"from_replicas": 2})
    assert rb.available and rb.arguments["replicas"] == 2
    assert default_rollback("scale_service", {"service": "x"}).available is False


async def test_memory_build_store_and_search() -> None:
    rt = build_runtime(seed=1, warmup=60)
    svc = IncidentMemoryService(
        rt.uow.memories, embeddings=HashEmbeddingProvider(32), llm=None, clock=SimClock(rt.engine)
    )
    inc = rt.incident(services=["api-gateway", "redis"])
    inc.number = 982
    hyp = Hypothesis(
        incident_id=inc.id,
        statement="order-service leaks redis connections",
        confidence=0.8,
        category=HypothesisCategory.RESOURCE_EXHAUSTION,
        suspected_root_cause_service="order-service",
    )
    plan = ActionPlan(
        incident_id=inc.id,
        hypothesis_id=hyp.id,
        tool_name="restart_service",
        arguments={"service": "order-service"},
        reason="r",
        expected_effect="e",
        verification=VerificationSpec(
            conditions=(VerificationCondition(metric="m", service="s", target=1),)
        ),
        status=ActionPlanStatus.VERIFIED,
    )
    memory = svc.build(inc, hypotheses=[hyp], plans=[plan], evidence=[], outcome="resolved")
    assert memory.root_cause_service == "order-service" and "restart_service" in memory.resolution
    assert memory.incident_number == 982
    await svc.store(memory)
    stored = await rt.uow.memories.get_for_incident(inc.id)
    assert stored is not None and stored.embedding is not None and len(stored.embedding) == 32
    other = svc.build(
        rt.incident(services=["payment-service"]),
        hypotheses=[
            Hypothesis(
                incident_id=uuid.uuid4(),
                statement="payment-service regression from 2.4.0",  # type: ignore[arg-type]
                category=HypothesisCategory.DEPLOYMENT_REGRESSION,
                suspected_root_cause_service="payment-service",
                confidence=0.9,
            )
        ],
        plans=[],
        evidence=[],
        outcome="resolved",
    )
    other.title = "payment errors after deploy"
    await svc.store(other)
    matches = await svc.search(
        SimilarIncidentQuery(
            text="redis connections saturated, gateway latency",
            affected_services=["api-gateway", "redis"],
            limit=2,
        )
    )
    assert matches and matches[0].memory.incident_id == inc.id
    assert matches[0].similarity > matches[-1].similarity or len(matches) == 1
    # exclusion works
    matches = await svc.search(
        SimilarIncidentQuery(text="redis connections", exclude_incident_id=inc.id, limit=3)
    )
    assert all(m.memory.incident_id != inc.id for m in matches)
    # lexical fallback without embeddings
    svc2 = IncidentMemoryService(rt.uow.memories, embeddings=None, llm=None)
    lex = await svc2.search(
        SimilarIncidentQuery(
            text="payment deploy regression", affected_services=["payment-service"]
        )
    )
    assert lex and lex[0].memory.root_cause_service == "payment-service"


async def test_memory_llm_summary_optional() -> None:
    rt = build_runtime(seed=1, warmup=30)
    from aegis.agent.schemas import MemorySummary

    llm = ScriptedProvider(
        queue=[
            MemorySummary(
                title="Redis leak in order-service",
                symptoms=["gw p95 up"],
                root_cause="order-service leaked redis connections",
                resolution="restart order-service",
                lessons=["add pool metrics"],
            )
        ]
    )
    svc = IncidentMemoryService(rt.uow.memories, llm=llm)
    inc = rt.incident()
    memory = svc.build(inc, hypotheses=[], plans=[], evidence=[], outcome="resolved")
    summary = await svc.summarize_with_llm(memory)
    assert summary is not None and summary.lessons == ["add pool metrics"]
    rebuilt = svc.build(
        inc, hypotheses=[], plans=[], evidence=[], outcome="resolved", summary=summary
    )
    assert rebuilt.title == "Redis leak in order-service" and rebuilt.lessons == [
        "add pool metrics"
    ]
    llm.fail_next = 1
    assert await svc.summarize_with_llm(memory) is None
    _ = Severity.SEV1
    _ = pytest


def test_remediation_fit_guard() -> None:
    import uuid

    from aegis.domain.enums import EvidenceKind, HypothesisCategory
    from aegis.domain.evidence import Evidence
    from aegis.domain.hypothesis import Hypothesis
    from aegis.remediation.planning import RemediationMismatch, validate_remediation_fit

    iid = uuid.uuid4()
    leak = Hypothesis(
        incident_id=iid,
        statement="order-service leaks redis connections",  # type: ignore[arg-type]
        category=HypothesisCategory.RESOURCE_EXHAUSTION,
        suspected_root_cause_service="order-service",
    )
    regression = leak.model_copy(
        update={
            "category": HypothesisCategory.DEPLOYMENT_REGRESSION,
            "suspected_root_cause_service": "payment-service",
        }
    )
    validate_remediation_fit("restart_service", leak, [], "order-service")
    validate_remediation_fit("rotate_connection_pool", leak, [], "order-service")
    with pytest.raises(RemediationMismatch, match="deployed recently"):
        validate_remediation_fit("rollback_deployment", leak, [], "order-service")
    with pytest.raises(RemediationMismatch, match="scale_service does not fit"):
        validate_remediation_fit("scale_service", leak, [], "order-service")
    # even a deployment_regression hypothesis needs the deployment in evidence
    with pytest.raises(RemediationMismatch, match="deployed recently"):
        validate_remediation_fit("rollback_deployment", regression, [], "payment-service")
    # a recent deployment in evidence legitimises a rollback whatever the category
    recent = Evidence(
        incident_id=iid,
        kind=EvidenceKind.DEPLOYMENT,
        source="inspect_deployment",  # type: ignore[arg-type]
        service="payment-service",
        title="deploy",
        summary="2.4.0 deployed 3 min ago",
        data={"recent": True},
        strength=0.85,
    )
    validate_remediation_fit(
        "rollback_deployment",
        leak.model_copy(update={"category": HypothesisCategory.UNKNOWN}),
        [recent],
        "payment-service",
    )


def test_evidence_overrides_a_mislabelled_mechanism() -> None:
    """The label a model puts on a hypothesis must not decide what may be executed. A crashed
    service is restarted, not rolled back or scaled, whatever the hypothesis called it."""
    import uuid

    from aegis.domain.enums import EvidenceKind, HypothesisCategory
    from aegis.domain.evidence import Evidence
    from aegis.domain.hypothesis import Hypothesis
    from aegis.remediation.planning import RemediationMismatch, validate_remediation_fit

    iid = uuid.uuid4()

    def ev(**kw: object) -> Evidence:
        base = {
            "incident_id": iid,
            "kind": EvidenceKind.DIAGNOSTIC,
            "source": "run_process_diagnostic",
            "service": "inventory-service",
            "title": "t",
            "summary": "s",
            "strength": 0.85,
        }
        base.update(kw)
        return Evidence(**base)  # type: ignore[arg-type]

    # the model called a crash a deployment regression
    mislabelled = Hypothesis(
        incident_id=iid,  # type: ignore[arg-type]
        statement="inventory-service shipped a bad release and is failing",
        category=HypothesisCategory.DEPLOYMENT_REGRESSION,
        suspected_root_cause_service="inventory-service",
    )
    crashed = [ev(data={"up": False, "crashed": True})]
    with pytest.raises(RemediationMismatch, match="deployed recently"):
        validate_remediation_fit("rollback_deployment", mislabelled, crashed, "inventory-service")
    with pytest.raises(RemediationMismatch, match="dependency_failure"):
        validate_remediation_fit("scale_service", mislabelled, crashed, "inventory-service")
    validate_remediation_fit("restart_service", mislabelled, crashed, "inventory-service")

    # a rollback is admissible only with a recent deployment in evidence
    deployed = [
        ev(
            kind=EvidenceKind.DEPLOYMENT,
            service="payment-service",
            source="inspect_deployment",
            data={"recent": True, "current_version": "2.4.0"},
        )
    ]
    validate_remediation_fit("rollback_deployment", mislabelled, deployed, "payment-service")
    stale = [ev(kind=EvidenceKind.DEPLOYMENT, service="payment-service", data={"recent": False})]
    with pytest.raises(RemediationMismatch, match="deployed recently"):
        validate_remediation_fit("rollback_deployment", mislabelled, stale, "payment-service")

    # health-check tags are read too: a failing process_up check means down
    tagged = [ev(kind=EvidenceKind.HEALTH, source="get_health", tags=["unhealthy", "process_up"])]
    with pytest.raises(RemediationMismatch, match="deployed recently"):
        validate_remediation_fit("rollback_deployment", mislabelled, tagged, "inventory-service")
