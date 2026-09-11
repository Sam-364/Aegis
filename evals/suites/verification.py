"""Verification eval: the engine must pass correct remediations and fail incorrect ones."""

from __future__ import annotations

from aegis.remediation.planning import build_verification_spec
from aegis.simulator.engine import SimulationEngine
from aegis.simulator.faults import SCENARIOS, RemediationRef
from aegis.verification.engine import VerificationEngine
from evals.harness.core import CaseResult, SuiteResult, run_cases
from evals.harness.world import build_world, detect


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


async def _case(scenario: str, ref: RemediationRef, expected: str, seed: int) -> CaseResult:
    world = build_world(seed=seed)
    incident = await detect(world, scenario, seconds=110)
    if incident is None:
        return CaseResult(
            name=f"{scenario}/{ref.action}:{ref.target}",
            passed=False,
            score=0.0,
            details={"reason": "not detected"},
        )

    async def sleeper(seconds: float) -> None:
        world.engine.advance(seconds)

    ve = VerificationEngine(
        world.container.telemetry, clock=world.container.clock, sleep=sleeper, poll_seconds=5
    )
    baselines = {
        (s.service, s.metric): await world.container.telemetry.baseline(s.service, s.metric)
        for s in incident.signals
    }
    spec = build_verification_spec(
        incident,
        baselines=baselines,
        target_service=ref.target or None,
        tool_spec=None,
        stabilization_seconds=20,
        timeout_seconds=150,
    )
    before = await ve.snapshot(spec)
    apply(world.engine, ref)
    result = await ve.verify(spec, before=before, baselines=baselines)
    passed = result.status.value == expected
    return CaseResult(
        name=f"{scenario}/{ref.action}:{ref.target}",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={"expected": expected, "got": result.status.value, "summary": result.summary[:120]},
    )


async def run() -> SuiteResult:
    cases = {}
    i = 0
    for sid, spec in SCENARIOS.items():
        if spec.expect_no_action or spec.expect_escalation:
            continue
        for ref in spec.correct_remediations[:1]:
            cases[f"{sid}-correct"] = lambda sid=sid, ref=ref, i=i: _case(
                sid, ref, "passed", 300 + i
            )
            i += 1
        for ref in spec.incorrect_remediations[:1]:
            cases[f"{sid}-incorrect"] = lambda sid=sid, ref=ref, i=i: _case(
                sid, ref, "failed", 300 + i
            )
            i += 1
    result = await run_cases("verification", cases, threshold=0.9, concurrency=3)
    return result
