"""Hypothesis-ranking eval: given evidence collected by the deterministic planner for each scenario,
does the scoring engine rank the true root cause first among all candidate services?"""

from __future__ import annotations

import uuid

from aegis.domain.enums import HypothesisCategory
from aegis.domain.flow import BudgetUsage
from aegis.hypotheses.engine import HypothesisEngine, HypothesisProposal
from aegis.simulator.faults import SCENARIOS
from evals.harness.core import CaseResult, SuiteResult, run_cases
from evals.harness.world import build_world, detect

CATEGORY_FOR = {
    "resource_exhaustion": HypothesisCategory.RESOURCE_EXHAUSTION,
    "deployment_regression": HypothesisCategory.DEPLOYMENT_REGRESSION,
    "dependency_failure": HypothesisCategory.DEPENDENCY_FAILURE,
    "capacity": HypothesisCategory.CAPACITY,
    "transient": HypothesisCategory.TRANSIENT,
    "network": HypothesisCategory.NETWORK,
}


async def _case(scenario: str, seed: int) -> CaseResult:
    spec = SCENARIOS[scenario]
    world = build_world(seed=seed)
    incident = await detect(world, scenario, seconds=100)
    if incident is None:
        return CaseResult(
            name=scenario, passed=False, score=0.0, details={"reason": "not detected"}
        )
    agent = world.container.agent
    flow = world.container.flows.get(
        incident.flow_name or "incident-investigation", incident.flow_version
    )
    usage = BudgetUsage()
    phase = flow.initial_phase
    # run triage + investigate deterministically to collect evidence
    for _ in range(2):
        out = await agent.run_phase(
            incident_id=incident.id,
            flow_name=flow.name,
            flow_version=flow.version,
            phase=phase,
            usage=usage,
        )
        usage = out.usage
        if out.next_phase and out.next_phase not in ("verify", "escalate"):
            phase = out.next_phase
    async with world.container.uow_factory() as uow:
        evidence = await uow.evidence.list_for_incident(incident.id)
        stored = await uow.incidents.get(incident.id)
    topology = await world.container.telemetry.topology()
    engine = HypothesisEngine()
    handles = {
        f"E{i + 1}": e
        for i, e in enumerate(sorted(evidence, key=lambda e: (e.created_at, str(e.id))))
    }
    evidence_map = {e.id: e for e in evidence}
    candidates = []
    for node in topology.nodes:
        support = [h for h, e in handles.items() if e.service == node.name] or [next(iter(handles))]
        proposal = HypothesisProposal(
            statement=f"{node.name} is the root cause of the incident",
            category=CATEGORY_FOR.get(spec.root_cause_category, HypothesisCategory.UNKNOWN),
            suspected_root_cause_service=node.name,
            mechanism="eval candidate",
            supporting_evidence=support,
        )
        h = engine.accept(
            proposal,
            incident=stored,
            resolve=handles,
            known_services=frozenset(n.name for n in topology.nodes),
            agent_run_id=uuid.uuid4(),
        )
        engine.rescore(
            h, evidence=evidence_map, incident=stored, topology=topology, now=world.engine.now
        )
        candidates.append(h)
    ranked = engine.rank(candidates)
    order = [h.suspected_root_cause_service for h in ranked]
    rank = (
        order.index(spec.root_cause_service) + 1 if spec.root_cause_service in order else len(order)
    )
    passed = rank == 1
    return CaseResult(
        name=scenario,
        passed=passed,
        score=1.0 / rank,
        details={
            "rank_of_truth": rank,
            "top3": order[:3],
            "evidence": len(evidence),
            "top_confidence": round(ranked[0].confidence, 3),
        },
    )


async def run() -> SuiteResult:
    targets = [s for s, spec in SCENARIOS.items() if not spec.expect_no_action]
    cases = {s: (lambda s=s, i=i: _case(s, seed=200 + i)) for i, s in enumerate(targets)}
    result = await run_cases("hypothesis-ranking", cases, threshold=0.8, concurrency=3)
    ranks = [c.details.get("rank_of_truth") for c in result.cases if c.details.get("rank_of_truth")]
    result.metrics = {"mrr": round(sum(1 / r for r in ranks) / len(ranks), 3) if ranks else 0}
    return result
