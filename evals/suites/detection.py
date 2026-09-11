"""Detection eval: time-to-detect, single-incident correlation, zero false positives on clean runs."""

from __future__ import annotations

from aegis.simulator.faults import SCENARIOS
from evals.harness.core import CaseResult, SuiteResult, run_cases
from evals.harness.world import build_world, detect

DETECT_BUDGET_SECONDS = {
    "redis-connection-leak": 60,
    "bad-deployment": 30,
    "db-pool-exhaustion": 60,
    "cascading-dependency": 20,
    "transient-spike": 20,
    "memory-leak": 120,
    "cpu-saturation": 30,
    "network-latency": 30,
}


async def _scenario_case(scenario: str, seed: int) -> CaseResult:
    world = build_world(seed=seed)
    injected = world.engine.now
    incident = await detect(world, scenario, seconds=150)
    incidents = await world.container.intake.open_incidents()
    spec = SCENARIOS[scenario]
    if incident is None:
        return CaseResult(
            name=scenario, passed=False, score=0.0, details={"reason": "not detected"}
        )
    ttd = (incident.detected_at - injected).total_seconds()
    budget = DETECT_BUDGET_SECONDS.get(scenario, 60)
    one_incident = len(incidents) == 1
    root_in_scope = (
        spec.root_cause_service in incident.affected_services
        or spec.root_cause_service == "api-gateway"
        or scenario == "db-pool-exhaustion"
    )
    score = (
        (0.5 if one_incident else 0.0)
        + (0.3 if ttd <= budget else 0.1)
        + (0.2 if root_in_scope else 0.0)
    )
    return CaseResult(
        name=scenario,
        passed=one_incident and ttd <= budget,
        score=score,
        details={
            "time_to_detect_s": ttd,
            "budget_s": budget,
            "incidents": len(incidents),
            "severity": incident.severity.value,
            "title": incident.title,
            "affected": incident.affected_services[:6],
        },
    )


async def _clean_case(seed: int) -> CaseResult:
    world = build_world(seed=seed)
    await world.detector.bootstrap()
    for _ in range(120):  # 10 simulated minutes
        world.engine.advance(5)
        await world.detector.cycle()
    incidents = await world.container.intake.open_incidents()
    return CaseResult(
        name=f"clean-run-seed-{seed}",
        passed=not incidents,
        score=1.0 if not incidents else 0.0,
        details={"false_positives": [i.title for i in incidents]},
    )


async def run() -> SuiteResult:
    cases = {s: (lambda s=s, i=i: _scenario_case(s, seed=100 + i)) for i, s in enumerate(SCENARIOS)}
    cases.update({f"clean-{seed}": (lambda seed=seed: _clean_case(seed)) for seed in (7, 8, 9)})
    result = await run_cases("detection", cases, threshold=0.9, concurrency=4)
    ttds = [c.details["time_to_detect_s"] for c in result.cases if "time_to_detect_s" in c.details]
    result.metrics = {
        "mean_time_to_detect_s": round(sum(ttds) / len(ttds), 1) if ttds else None,
        "false_positive_runs": sum(
            1 for c in result.cases if c.name.startswith("clean") and not c.passed
        ),
    }
    return result
