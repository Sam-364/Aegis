from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from aegis.domain.enums import (
    EvidenceKind,
    HypothesisCategory,
    HypothesisStatus,
    Severity,
    SignalKind,
)
from aegis.domain.errors import LLMUnavailable
from aegis.domain.evidence import Evidence
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.telemetry import DependencyEdge, ServiceNode, Topology
from aegis.evidence.service import digest, handles_for
from aegis.hypotheses.engine import (
    HypothesisEngine,
    HypothesisProposal,
    ProposalRejected,
    SuggestedTestProposal,
    deterministic_hypotheses,
    noisy_or,
)
from aegis.llm.breaker import CircuitBreaker
from aegis.llm.provider_scripted import HashEmbeddingProvider, ScriptedProvider

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def topo() -> Topology:
    return Topology(
        nodes=tuple(
            ServiceNode(name=n, kind=k)
            for n, k in [
                ("api-gateway", "gateway"),
                ("order-service", "service"),
                ("redis", "cache"),
                ("payment-service", "service"),
                ("postgres", "database"),
            ]
        ),
        edges=(
            DependencyEdge(source="api-gateway", target="order-service"),
            DependencyEdge(source="api-gateway", target="payment-service"),
            DependencyEdge(source="order-service", target="redis"),
            DependencyEdge(source="order-service", target="payment-service"),
            DependencyEdge(source="payment-service", target="postgres"),
        ),
    )


def incident() -> Incident:
    sig = AnomalySignal(
        service="api-gateway",
        metric="latency_p95_ms",
        kind=SignalKind.LATENCY,
        observed_value=2400,
        baseline_value=200,
        deviation_sigma=12,
        detector="t",
        detected_at=NOW,
        window_seconds=15,
        description="gw latency",
    )
    sig2 = sig.model_copy(
        update={"service": "redis", "metric": "connections", "kind": SignalKind.SATURATION}
    )
    return Incident(
        title="t",
        severity=Severity.SEV2,
        signals=[sig, sig2],
        affected_services=["api-gateway", "redis"],
        detected_at=NOW,
    )


def ev(
    inc: Incident,
    kind: EvidenceKind,
    service: str | None,
    strength: float,
    *,
    at: datetime | None = None,
    data: dict | None = None,
    tags: list[str] | None = None,
    title: str = "t",
) -> Evidence:
    return Evidence(
        incident_id=inc.id,
        kind=kind,
        source="tool",
        service=service,
        title=title,
        summary=f"{title} summary about {service}",
        strength=strength,
        observed_at=at or NOW + timedelta(seconds=30),
        data=data or {},
        tags=tags or [],
    )


def test_noisy_or() -> None:
    assert noisy_or([]) == 0
    assert noisy_or([0.5, 0.5]) == pytest.approx(0.75)
    assert noisy_or([1.0, 0.2]) == 1.0


def test_proposal_acceptance_and_rejection() -> None:
    inc = incident()
    e1 = ev(inc, EvidenceKind.DIAGNOSTIC, "order-service", 0.9)
    handles = {"E1": e1}
    engine = HypothesisEngine()
    known = frozenset({"api-gateway", "order-service", "redis"})
    good = HypothesisProposal(
        statement="order-service leaks redis connections and saturates redis",
        category=HypothesisCategory.RESOURCE_EXHAUSTION,
        suspected_root_cause_service="order-service",
        mechanism="leak",
        supporting_evidence=["E1", "E99"],
        suggested_tests=[SuggestedTestProposal(tool_name="run_cache_diagnostic", expectation="x")],
    )
    h = engine.accept(good, incident=inc, resolve=handles, known_services=known, agent_run_id=None)
    assert h.supporting_evidence_ids == [e1.id]  # unknown handle dropped
    with pytest.raises(ProposalRejected):
        engine.accept(
            good.model_copy(update={"suspected_root_cause_service": "mars"}),
            incident=inc,
            resolve=handles,
            known_services=known,
            agent_run_id=None,
        )
    with pytest.raises(ProposalRejected):
        engine.accept(
            good.model_copy(update={"supporting_evidence": ["E42"]}),
            incident=inc,
            resolve=handles,
            known_services=known,
            agent_run_id=None,
        )


def test_scoring_prefers_root_cause_that_explains_symptoms() -> None:
    inc = incident()
    engine = HypothesisEngine()
    redis_diag = ev(
        inc,
        EvidenceKind.DIAGNOSTIC,
        "order-service",
        0.9,
        title="redis connection analysis",
        data={"top_client": "order-service"},
    )
    gw_metric = ev(inc, EvidenceKind.METRIC, "api-gateway", 0.8)
    evidence = {redis_diag.id: redis_diag, gw_metric.id: gw_metric}
    known = frozenset({"api-gateway", "order-service", "redis", "payment-service"})
    handles = {"E1": redis_diag, "E2": gw_metric}

    def propose(root: str) -> HypothesisProposal:
        return HypothesisProposal(
            statement=f"{root} is the root cause of the latency",
            category=HypothesisCategory.RESOURCE_EXHAUSTION,
            suspected_root_cause_service=root,
            mechanism="m",
            supporting_evidence=["E1", "E2"],
        )

    good = engine.accept(
        propose("order-service"),
        incident=inc,
        resolve=handles,
        known_services=known,
        agent_run_id=None,
    )
    wrong = engine.accept(
        propose("payment-service"),
        incident=inc,
        resolve=handles,
        known_services=known,
        agent_run_id=None,
    )
    upstream = engine.accept(
        propose("api-gateway"),
        incident=inc,
        resolve=handles,
        known_services=known,
        agent_run_id=None,
    )
    for h in (good, wrong, upstream):
        engine.rescore(
            h, evidence=evidence, incident=inc, topology=topo(), now=NOW + timedelta(minutes=1)
        )
    assert good.confidence > wrong.confidence
    assert good.confidence > upstream.confidence
    assert good.score.dependency_alignment == 1.0  # order-service explains gateway and redis
    assert wrong.score.dependency_alignment == 0.5  # payment explains the gateway but not redis
    assert good.status is HypothesisStatus.SUPPORTED
    assert any("explains 2/2" in x for x in good.score.explanation)


def test_contradiction_and_tests_move_confidence() -> None:
    inc = incident()
    engine = HypothesisEngine()
    support = ev(inc, EvidenceKind.DIAGNOSTIC, "order-service", 0.8)
    contra = ev(inc, EvidenceKind.DIAGNOSTIC, "order-service", 0.9, title="pool ok")
    evidence = {support.id: support, contra.id: contra}
    h = engine.accept(
        HypothesisProposal(
            statement="order-service leaks connections to redis",
            category=HypothesisCategory.RESOURCE_EXHAUSTION,
            suspected_root_cause_service="order-service",
            mechanism="m",
            supporting_evidence=["E1"],
        ),
        incident=inc,
        resolve={"E1": support, "E2": contra},
        known_services=frozenset({"order-service"}),
        agent_run_id=None,
    )
    engine.rescore(h, evidence=evidence, incident=inc, topology=topo(), now=NOW)
    base = h.confidence
    h.contradicting_evidence_ids = [contra.id]
    engine.rescore(h, evidence=evidence, incident=inc, topology=topo(), now=NOW)
    assert h.confidence < base
    h.contradicting_evidence_ids = []
    produced = [
        ev(inc, EvidenceKind.DIAGNOSTIC, "order-service", 0.9, data={"top_client": "order-service"})
    ]
    t = engine.judge_test(
        h,
        produced,
        "confirmed",
        tool_name="run_cache_diagnostic",
        expectation="x",
        tool_execution_id=None,
        now=NOW,
    )
    assert t.outcome == "confirmed"
    engine.rescore(h, evidence=evidence, incident=inc, topology=topo(), now=NOW)
    assert h.confidence > base and h.status is HypothesisStatus.CONFIRMED
    # a 'confirmed' verdict that does not implicate the suspected service is downgraded
    other = [ev(inc, EvidenceKind.DIAGNOSTIC, "payment-service", 0.9)]
    t2 = engine.judge_test(
        h, other, "confirmed", tool_name="x", expectation="x", tool_execution_id=None, now=NOW
    )
    assert t2.outcome == "inconclusive" and "downgraded" in t2.detail
    t3 = engine.judge_test(
        h, [], "refuted", tool_name="x", expectation="x", tool_execution_id=None, now=NOW
    )
    assert t3.outcome == "inconclusive"


def test_deployment_temporal_alignment() -> None:
    inc = incident()
    engine = HypothesisEngine()
    before = ev(
        inc,
        EvidenceKind.DEPLOYMENT,
        "payment-service",
        0.85,
        data={"deployed_at": (NOW - timedelta(minutes=5)).isoformat()},
    )
    after = ev(
        inc,
        EvidenceKind.DEPLOYMENT,
        "payment-service",
        0.85,
        data={"deployed_at": (NOW + timedelta(minutes=5)).isoformat()},
    )
    for e, expected in ((before, 1.0), (after, 0.3)):
        h = engine.accept(
            HypothesisProposal(
                statement="payment-service regression from deployment",
                category=HypothesisCategory.DEPLOYMENT_REGRESSION,
                suspected_root_cause_service="payment-service",
                mechanism="m",
                supporting_evidence=["E1"],
            ),
            incident=inc,
            resolve={"E1": e},
            known_services=frozenset({"payment-service"}),
            agent_run_id=None,
        )
        engine.rescore(
            h, evidence={e.id: e}, incident=inc, topology=topo(), now=NOW + timedelta(minutes=10)
        )
        assert h.score.temporal_alignment == expected


def test_deterministic_fallback_proposals() -> None:
    inc = incident()
    items = [
        ev(
            inc, EvidenceKind.DIAGNOSTIC, "order-service", 0.9, title="redis saturation connections"
        ),
        ev(inc, EvidenceKind.DEPLOYMENT, "payment-service", 0.85, title="deploy"),
        ev(inc, EvidenceKind.HEALTH, "api-gateway", 0.3),
    ]
    proposals = deterministic_hypotheses(inc, items, topo())
    assert [p.suspected_root_cause_service for p in proposals] == [
        "order-service",
        "payment-service",
    ]
    assert proposals[0].category is HypothesisCategory.RESOURCE_EXHAUSTION
    assert proposals[1].category is HypothesisCategory.DEPLOYMENT_REGRESSION


def test_digest_handles_are_stable_and_truncate() -> None:
    inc = incident()
    items = [
        ev(
            inc,
            EvidenceKind.METRIC,
            "api-gateway",
            0.1 * (i % 9 + 1),
            at=NOW + timedelta(seconds=i),
            title=f"m{i}",
        )
        for i in range(60)
    ]
    for i, e in enumerate(items):
        e.created_at = NOW + timedelta(seconds=i)
    d = digest(items, limit=20)
    assert len(d.lines) == 20
    assert d.lines[0].startswith("[E")
    assert handles_for(items)["E1"].title == "m0"
    assert digest([]).text() == "(no evidence collected yet)"


class Answer(BaseModel):
    x: int


async def test_scripted_provider_and_breaker() -> None:
    p = ScriptedProvider(queue=[Answer(x=1), {"x": 2}])
    r = await p.complete_structured(schema=Answer, system="s", user="u")
    assert r.value.x == 1 and r.usage.model == "scripted-reasoner"
    r = await p.complete_structured(schema=Answer, system="s", user="u", tier="fast")
    assert r.value.x == 2
    with pytest.raises(LLMUnavailable):
        await p.complete_structured(schema=Answer, system="s", user="u")
    assert len(p.calls) == 3
    b = CircuitBreaker(failures=2, reset_seconds=1000)
    b.record_failure()
    assert not b.open
    b.record_failure()
    assert b.open
    b.record_success()
    assert not b.open
    emb = HashEmbeddingProvider(16)
    vecs = await emb.embed(
        ["redis connections leak", "redis connection leak", "deployment rollback"]
    )
    dot = sum(a * b for a, b in zip(vecs[0], vecs[1], strict=True))
    dot2 = sum(a * b for a, b in zip(vecs[0], vecs[2], strict=True))
    assert dot > dot2
    _ = uuid.uuid4()
