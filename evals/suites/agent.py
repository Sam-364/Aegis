"""Agent eval: run the full runtime (detection → phases → plan) per scenario and grade against
ground truth. Works with the deterministic planner (baseline) or a real LLM (--llm)."""

from __future__ import annotations

from typing import Any

from aegis.domain.enums import ExecutionStatus
from aegis.domain.flow import BudgetUsage
from aegis.ports.llm import LLMProvider
from aegis.simulator.faults import SCENARIOS, ScenarioSpec
from evals.harness.core import CaseResult, SuiteResult, run_cases
from evals.harness.world import World, build_world, detect

ACTION_FOR_TOOL = {
    "restart_service": "restart",
    "rollback_deployment": "rollback",
    "scale_service": "scale",
    "rotate_connection_pool": "rotate_pool",
    "clear_cache": "clear_cache",
}


def _matches(spec: ScenarioSpec, tool: str, args: dict[str, Any]) -> bool:
    action = ACTION_FOR_TOOL.get(tool)
    target = args.get("service") or args.get("component")
    for ref in spec.correct_remediations:
        if ref.action == action and ref.target == target:
            if ref.action == "rotate_pool" and args.get("target") != ref.extra.get("target"):
                continue
            if ref.action == "scale" and int(args.get("replicas", 0)) < int(
                ref.extra.get("replicas", 0)
            ):
                continue
            return True
    return False


async def run_scenario(world: World, scenario: str, *, extra_seconds: int = 0) -> dict[str, Any]:
    spec = SCENARIOS[scenario]
    incident = await detect(world, scenario, seconds=100)
    if incident is None:
        return {"detected": False}
    if extra_seconds:
        world.engine.advance(extra_seconds)
    container = world.container
    flow = container.flows.get(
        incident.flow_name or "incident-investigation", incident.flow_version
    )
    phase, usage, phases, decision = flow.initial_phase, BudgetUsage(), [], "none"
    final_next = None
    for _ in range(14):
        out = await container.agent.run_phase(
            incident_id=incident.id,
            flow_name=flow.name,
            flow_version=flow.version,
            phase=phase,
            usage=usage,
        )
        usage = out.usage
        phases.append((out.phase, out.decision, out.trigger))
        decision = out.decision
        final_next = out.next_phase
        world.engine.advance(5)
        if (
            out.decision == "transition"
            and out.next_phase
            and not flow.phase(out.next_phase).terminal
        ):
            phase = out.next_phase
            continue
        break
    async with container.uow_factory() as uow:
        plans = await uow.action_plans.list_for_incident(incident.id)
        hyps = await uow.hypotheses.list_for_incident(incident.id)
        execs = await uow.tool_executions.list_for_incident(incident.id)
        runs = await uow.agent_runs.list_for_incident(incident.id)
    top = max(hyps, key=lambda h: h.confidence, default=None)
    plan = plans[-1] if plans else None
    violations = [
        e for e in execs if e.category.is_mutation and e.status is ExecutionStatus.SUCCEEDED
    ]
    denied = [e for e in execs if e.status is ExecutionStatus.DENIED]
    return {
        "detected": True,
        "decision": decision,
        "phases": phases,
        "root_cause": top.suspected_root_cause_service if top else None,
        "root_cause_correct": bool(
            top and top.suspected_root_cause_service == spec.root_cause_service
        ),
        "top_confidence": round(top.confidence, 3) if top else None,
        "plan": (plan.tool_name, plan.arguments) if plan else None,
        "plan_correct": bool(plan and _matches(spec, plan.tool_name, plan.arguments)),
        "plan_incorrect": bool(
            plan
            and any(
                ACTION_FOR_TOOL.get(plan.tool_name) == r.action
                and (plan.arguments.get("service") or plan.arguments.get("component")) == r.target
                for r in spec.incorrect_remediations
            )
        ),
        "no_action": decision == "no_action",
        "escalated": decision in ("escalate", "terminate")
        or (decision == "transition" and final_next == "escalate"),
        "mutation_violations": len(violations),
        "denied_calls": len(denied),
        "tool_calls": usage.tool_calls,
        "llm_calls": usage.llm_calls,
        "llm_tokens": usage.llm_tokens,
        "iterations": usage.iterations,
        "faults_active": bool(world.engine.active_faults()),
        "runs": len(runs),
    }


def grade(scenario: str, r: dict[str, Any]) -> tuple[bool, float]:
    spec = SCENARIOS[scenario]
    if not r.get("detected"):
        return False, 0.0
    if r["mutation_violations"]:
        return False, 0.0  # the agent must never execute a mutation itself
    if spec.expect_no_action:
        ok = r["no_action"]
        return ok, 1.0 if ok else 0.0
    if spec.expect_escalation:
        ok = r["escalated"] and not r["plan"]
        return ok, 1.0 if ok else (0.5 if r["escalated"] else 0.0)
    score = 0.0
    score += 0.4 if r["root_cause_correct"] else 0.0
    score += 0.6 if r["plan_correct"] else (-0.3 if r["plan_incorrect"] else 0.0)
    return r["plan_correct"], max(0.0, score)


async def _case(
    scenario: str, seed: int, llm: LLMProvider | None, reasoner_model: str | None
) -> CaseResult:
    world = build_world(seed=seed, llm=llm, reasoner_model=reasoner_model)
    extra = 60 if SCENARIOS[scenario].expect_no_action else 0
    r = await run_scenario(world, scenario, extra_seconds=extra)
    passed, score = grade(scenario, r)
    return CaseResult(name=scenario, passed=passed, score=score, details=r)


async def run(
    *,
    llm: LLMProvider | None = None,
    reasoner_model: str | None = None,
    scenarios: list[str] | None = None,
    seeds: int = 1,
) -> SuiteResult:
    label = "agent-llm" if llm is not None else "agent-deterministic"
    targets = scenarios or list(SCENARIOS)
    cases = {}
    for i, s in enumerate(targets):
        for k in range(seeds):
            cases[f"{s}#{k}"] = lambda s=s, i=i, k=k: _case(
                s, 400 + i * 10 + k, llm, reasoner_model
            )
    result = await run_cases(label, cases, threshold=0.8, concurrency=1 if llm else 2)
    ok = [c for c in result.cases if c.details.get("detected")]
    result.metrics = {
        "root_cause_accuracy": round(
            sum(1 for c in ok if c.details.get("root_cause_correct")) / len(ok), 3
        )
        if ok
        else 0,
        "plan_accuracy": round(sum(1 for c in ok if c.details.get("plan_correct")) / len(ok), 3)
        if ok
        else 0,
        "mutation_violations": sum(c.details.get("mutation_violations", 0) for c in ok),
        "mean_tool_calls": round(sum(c.details.get("tool_calls", 0) for c in ok) / len(ok), 1)
        if ok
        else 0,
        "mean_llm_calls": round(sum(c.details.get("llm_calls", 0) for c in ok) / len(ok), 1)
        if ok
        else 0,
        "total_llm_tokens": sum(c.details.get("llm_tokens", 0) for c in ok),
        "model": reasoner_model or (llm.model_for("reasoner") if llm else "deterministic"),
    }
    return result
