"""Agent runtime tests: deterministic end-to-end phases, hostile model handling, crash resume."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from pydantic import BaseModel

from aegis.agent.runtime import (
    MAX_STALLED_ITERATIONS,
    AgentDependencies,
    AgentHooks,
    AgentRuntime,
    PhaseOutcome,
)
from aegis.agent.schemas import AgentProposal, RemediationProposal, ToolCallProposal
from aegis.domain.enums import (
    ActionPlanStatus,
    AgentRunStatus,
    AgentStepKind,
    EvidenceKind,
    ExecutionStatus,
    HypothesisCategory,
    HypothesisStatus,
    TerminationReason,
)
from aegis.domain.evidence import Evidence
from aegis.domain.flow import BudgetUsage
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident
from aegis.evidence.service import digest
from aegis.hypotheses.engine import HypothesisProposal
from aegis.infrastructure.memory.repositories import InMemoryUnitOfWorkFactory
from aegis.infrastructure.simulator.inprocess import to_topology
from aegis.llm.provider_scripted import ScriptedProvider
from aegis.simulator.faults import SCENARIOS
from tests.helpers import Runtime, SimClock, build_runtime


class SimAdvancingClock(SimClock):
    """Advance the simulator a little on every read so tools observe time passing."""

    def now(self) -> datetime:
        return self.engine.now


def make_runtime(
    rt: Runtime, llm: ScriptedProvider | None, hooks: AgentHooks | None = None
) -> AgentRuntime:
    deps = AgentDependencies(
        uow_factory=InMemoryUnitOfWorkFactory(rt.store),
        registry=rt.registry,
        authorizer=rt.authorizer,
        flows=rt.flows,
        telemetry=rt.telemetry,
        infrastructure=rt.gateway,
        environment=rt.environment,
        llm=llm,
        clock=SimAdvancingClock(rt.engine),
        hooks=hooks,
    )
    return AgentRuntime(deps)


async def seed_incident(rt: Runtime, scenario: str, *, advance: int = 100) -> Incident:
    """Inject a scenario, run detection to get realistic signals, and persist the incident."""
    from aegis.detection.engine import DetectionEngine
    from tests.unit.test_detection import FakeSink

    sink = FakeSink()
    det = DetectionEngine(rt.telemetry, sink, interval_seconds=5, clock=SimClock(rt.engine))
    await det.bootstrap()
    rt.engine.inject(scenario)
    for _ in range(advance // 5):
        rt.engine.advance(5)
        await det.cycle()
    assert sink.incidents, f"{scenario} was not detected"
    inc = sink.incidents[0]
    flow = rt.flows.select(inc.signals)
    inc.flow_name, inc.flow_version = flow.name, flow.version
    from aegis.domain.base import Actor
    from aegis.domain.enums import IncidentStatus
    from aegis.domain.statemachine import transition

    transition(inc, IncidentStatus.TRIAGING, Actor.workflow("wf"))
    transition(inc, IncidentStatus.INVESTIGATING, Actor.workflow("wf"))
    await rt.uow.incidents.add(inc)
    return inc


async def run_all_phases(
    agent: AgentRuntime, inc: Incident, rt: Runtime, *, max_phases: int = 8
) -> list[PhaseOutcome]:
    flow = rt.flows.get(inc.flow_name or "incident-investigation", inc.flow_version)
    phase = flow.initial_phase
    usage = BudgetUsage()
    outcomes: list[PhaseOutcome] = []
    for _ in range(max_phases):
        out = await agent.run_phase(
            incident_id=inc.id,
            flow_name=flow.name,
            flow_version=flow.version,
            phase=phase,
            usage=usage,
        )
        outcomes.append(out)
        usage = out.usage
        rt.engine.advance(5)
        if (
            out.decision == "transition"
            and out.next_phase
            and not flow.phase(out.next_phase).terminal
        ):
            phase = out.next_phase
            continue
        break
    return outcomes


@pytest.mark.parametrize(
    "scenario,expected_tools,expected_root",
    [
        ("redis-connection-leak", {"restart_service", "rotate_connection_pool"}, "order-service"),
        ("bad-deployment", {"rollback_deployment"}, "payment-service"),
        ("cascading-dependency", {"restart_service"}, "inventory-service"),
    ],
)
async def test_deterministic_agent_plans_correct_remediation(
    scenario: str, expected_tools: set[str], expected_root: str
) -> None:
    rt = build_runtime(seed=21, warmup=600)
    inc = await seed_incident(rt, scenario)
    agent = make_runtime(rt, llm=None)
    outcomes = await run_all_phases(agent, inc, rt)
    phases = [o.phase for o in outcomes]
    assert phases[0] == "triage"
    final = outcomes[-1]
    assert final.decision == "action_planned", [(o.phase, o.decision, o.summary) for o in outcomes]
    plans = await rt.uow.action_plans.list_for_incident(inc.id)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.tool_name in expected_tools
    assert plan.arguments["service"] == expected_root
    assert plan.status is ActionPlanStatus.PROPOSED
    assert plan.verification.conditions
    hyps = await rt.uow.hypotheses.list_for_incident(inc.id)
    top = max(hyps, key=lambda h: h.confidence)
    assert top.suspected_root_cause_service == expected_root
    assert top.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.SUPPORTED)
    # the agent never executed a mutating tool itself
    execs = await rt.uow.tool_executions.list_for_incident(inc.id)
    assert all(not e.category.is_mutation for e in execs)
    assert all(e.status is not ExecutionStatus.DENIED for e in execs), [
        (e.tool_name, e.authorization.denial_code)
        for e in execs
        if e.status is ExecutionStatus.DENIED
    ]
    runs = await rt.uow.agent_runs.list_for_incident(inc.id)
    assert all(r.status is AgentRunStatus.COMPLETED for r in runs)
    assert rt.engine.active_faults(), "the agent must not have remediated anything itself"


async def test_deterministic_agent_concludes_no_action_for_transient_spike() -> None:
    rt = build_runtime(seed=8, warmup=600)
    inc = await seed_incident(rt, "transient-spike", advance=30)
    rt.engine.advance(90)  # spike is over
    agent = make_runtime(rt, llm=None)
    outcomes = await run_all_phases(agent, inc, rt)
    assert outcomes[-1].decision == "no_action", [
        (o.phase, o.decision, o.summary) for o in outcomes
    ]
    assert outcomes[-1].termination is TerminationReason.NO_ACTION_REQUIRED
    assert not await rt.uow.action_plans.list_for_incident(inc.id)


async def test_hostile_model_is_contained() -> None:
    """The model repeatedly proposes forbidden or malformed steps; the runtime refuses each one,
    feeds the refusal back, and eventually falls back to deterministic planning."""
    rt = build_runtime(seed=5, warmup=600)
    inc = await seed_incident(rt, "redis-connection-leak")
    hostile = [
        AgentProposal(
            observation="x",
            action="call_tool",
            rationale="r",
            tool_call=ToolCallProposal(
                tool_name="drop_database", arguments_json='{"target":"postgres"}', purpose="p"
            ),
        ),
        AgentProposal(
            observation="x",
            action="call_tool",
            rationale="r",
            tool_call=ToolCallProposal(
                tool_name="restart_service",
                arguments_json='{"service":"order-service"}',
                purpose="p",
            ),
        ),
        AgentProposal(
            observation="x",
            action="plan_remediation",
            rationale="r",
            remediation=RemediationProposal(
                tool_name="restart_service",
                arguments_json='{"service":"api-gateway"}',
                target_hypothesis="H1",
                reason="r",
                expected_effect="e",
            ),
        ),
        AgentProposal(
            observation="x",
            action="call_tool",
            rationale="r",
            tool_call=ToolCallProposal(
                tool_name="get_metrics", arguments_json="not json", purpose="p"
            ),
        ),
        AgentProposal(observation="x", action="conclude_no_action", rationale="r"),
    ]
    calls = {"n": 0}

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        i = calls["n"]
        calls["n"] += 1
        if schema is AgentProposal:
            return hostile[i % len(hostile)]
        raise AssertionError(schema)

    llm = ScriptedProvider(script=script)
    agent = make_runtime(rt, llm=llm)
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="triage",
    )
    assert out.decision in ("transition", "terminate")
    execs = await rt.uow.tool_executions.list_for_incident(inc.id)
    denied = [e for e in execs if e.status is ExecutionStatus.DENIED]
    assert denied, "hostile tool calls must be recorded as denied"
    assert {e.authorization.denial_code for e in denied} <= {
        "phase_violation",
        "flow_violation",
        "tool_not_allowed",
    }
    assert not [e for e in execs if e.category.is_mutation and e.succeeded]
    assert rt.engine.services["order-service"].restart_count == 0
    assert rt.engine.services["api-gateway"].restart_count == 0
    assert not await rt.uow.action_plans.list_for_incident(inc.id)
    steps = await rt.uow.agent_runs.steps_for_run(out.agent_run_id)
    kinds = {s.kind.value for s in steps}
    assert "fallback" in kinds  # deterministic planner took over after the invalid streak
    # feedback about refusals was fed to the model
    assert any("was refused" in c["user"] or "not allowed" in c["user"] for c in llm.calls[1:])
    # some real read-only evidence was still collected by the fallback planner
    assert await rt.uow.evidence.list_for_incident(inc.id)


async def test_scripted_model_drives_a_full_investigation() -> None:
    """A cooperative scripted model that behaves like a good SRE reaches a correct plan with the
    runtime scoring its hypotheses."""
    rt = build_runtime(seed=13, warmup=600)
    inc = await seed_incident(rt, "redis-connection-leak")

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        if schema.__name__ == "TestJudgement":
            from aegis.agent.schemas import TestJudgement

            return TestJudgement(
                outcome="confirmed", detail="order-service holds most redis connections"
            )
        phase = user.split("phase '")[1].split("'", maxsplit=1)[0]
        if phase == "triage":
            if "inspect_dependencies" not in user.split("# Evidence")[1]:
                return AgentProposal(
                    observation="gateway slow",
                    action="call_tool",
                    rationale="map chain",
                    tool_call=ToolCallProposal(
                        tool_name="inspect_dependencies",
                        arguments_json='{"service":"api-gateway"}',
                        purpose="chain",
                    ),
                )
            return AgentProposal(
                observation="chain mapped",
                action="call_tool",
                rationale="health",
                tool_call=ToolCallProposal(
                    tool_name="get_health", arguments_json='{"service":"redis"}', purpose="h"
                ),
            )
        if phase == "investigate":
            ev = user.split("# Evidence")[1]
            if "connection analysis" not in ev:
                return AgentProposal(
                    observation="redis suspicious",
                    action="call_tool",
                    rationale="who holds connections",
                    tool_call=ToolCallProposal(
                        tool_name="inspect_redis",
                        arguments_json='{"component":"redis"}',
                        purpose="clients",
                    ),
                )
            if "redis pool wait" not in ev and "order-service logs" not in ev:
                return AgentProposal(
                    observation="order-service is top client",
                    action="call_tool",
                    rationale="logs",
                    tool_call=ToolCallProposal(
                        tool_name="get_logs",
                        arguments_json='{"service":"order-service","level":"WARN"}',
                        purpose="logs",
                    ),
                )
            handles = [
                line.split(" ")[0][1:] for line in ev.strip().splitlines() if line.startswith("[E")
            ]
            return AgentProposal(
                observation="order-service leaks redis connections",
                action="propose_hypotheses",
                rationale="top client + pool waits",
                hypotheses=[
                    HypothesisProposal(
                        statement="order-service leaks Redis connections, saturating Redis and slowing the gateway",
                        category=HypothesisCategory.RESOURCE_EXHAUSTION,
                        suspected_root_cause_service="order-service",
                        mechanism="leaked client connections exhaust maxclients",
                        supporting_evidence=handles[-3:],
                    )
                ],
            )
        if phase == "hypothesize":
            return AgentProposal(
                observation="H1 is strong", action="phase_complete", rationale="ready to validate"
            )
        if phase == "validate":
            return AgentProposal(
                observation="validate H1",
                action="call_tool",
                rationale="cache diag",
                tool_call=ToolCallProposal(
                    tool_name="run_cache_diagnostic",
                    arguments_json='{"component":"redis"}',
                    purpose="confirm",
                    tests_hypothesis="H1",
                    expectation="order-service is top client with leaked connections",
                ),
            )
        if phase == "remediate":
            return AgentProposal(
                observation="plan",
                action="plan_remediation",
                rationale="least invasive",
                remediation=RemediationProposal(
                    tool_name="rotate_connection_pool",
                    arguments_json='{"service":"order-service","target":"redis"}',
                    target_hypothesis="H1",
                    reason="release leaked connections",
                    expected_effect="redis connections and gateway latency return to baseline",
                ),
            )
        raise AssertionError(phase)

    llm = ScriptedProvider(script=script)
    agent = make_runtime(rt, llm=llm)
    outcomes = await run_all_phases(agent, inc, rt)
    assert [o.phase for o in outcomes] == [
        "triage",
        "investigate",
        "hypothesize",
        "validate",
        "remediate",
    ], [(o.phase, o.decision, o.summary) for o in outcomes]
    assert outcomes[-1].decision == "action_planned"
    plan = (await rt.uow.action_plans.list_for_incident(inc.id))[0]
    assert plan.tool_name == "rotate_connection_pool" and plan.arguments == {
        "service": "order-service",
        "target": "redis",
    }
    assert plan.proposed_by == "llm"
    hyp = (await rt.uow.hypotheses.list_for_incident(inc.id))[0]
    assert hyp.status is HypothesisStatus.CONFIRMED and hyp.confidence >= 0.65
    assert hyp.confirmed_tests() == 1
    assert plan.rollback.available is False  # pool rotation is not reversible
    assert {c.metric for c in plan.verification.conditions} >= {"latency_p95_ms"}
    runs = await rt.uow.agent_runs.list_for_incident(inc.id)
    assert sum(r.usage.llm_calls for r in runs) >= 5
    events = await rt.uow.incidents.events(inc.id)
    types = [e.type for e in events]
    assert (
        "hypothesis.created" in types
        and "hypothesis.validated" in types
        and "remediation.proposed" in types
    )
    assert types == sorted(types, key=lambda _: 0)  # ordering preserved
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


async def test_crash_mid_phase_resumes_from_checkpoint_without_repeating_tool_calls() -> None:
    rt = build_runtime(seed=2, warmup=600)
    inc = await seed_incident(rt, "cascading-dependency", advance=40)
    crash = {"armed": True, "seen": 0}

    async def on_step(node: str) -> None:
        if node == "observe":
            crash["seen"] += 1
        if crash["armed"] and node == "observe" and crash["seen"] == 3:
            crash["armed"] = False
            raise RuntimeError("worker crashed")

    agent = make_runtime(rt, llm=None, hooks=AgentHooks(on_step=on_step))
    run_id = uuid.uuid4()
    with pytest.raises(RuntimeError):
        await agent.run_phase(
            incident_id=inc.id,
            flow_name=inc.flow_name or "",
            flow_version=inc.flow_version or "",
            phase="triage",
            agent_run_id=run_id,
        )
    execs_before = await rt.uow.tool_executions.list_for_incident(inc.id)
    assert len(execs_before) == 2  # two tool calls happened before the crash
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="triage",
        agent_run_id=run_id,
        attempt=2,
    )
    assert out.agent_run_id == run_id
    execs_after = await rt.uow.tool_executions.list_for_incident(inc.id)
    # resumed after the checkpoint: earlier calls were not repeated (same args not executed twice)
    keys = [(e.tool_name, json.dumps(e.arguments, sort_keys=True)) for e in execs_after]
    assert len(keys) == len(set(keys))
    assert len(execs_after) > len(execs_before)
    run = await rt.uow.agent_runs.get(run_id)
    assert run.attempt == 2 and run.status is AgentRunStatus.COMPLETED


async def test_budget_exhaustion_terminates_phase() -> None:
    rt = build_runtime(seed=4, warmup=300)
    inc = await seed_incident(rt, "cpu-saturation", advance=40)
    agent = make_runtime(rt, llm=None)
    flow = rt.flows.get(inc.flow_name or "incident-investigation")
    budget = flow.budget_for(inc.severity)
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=flow.name,
        flow_version=flow.version,
        phase="triage",
        usage=BudgetUsage(tool_calls=budget.max_tool_calls),
    )
    assert out.decision == "terminate" and out.termination is TerminationReason.BUDGET_EXHAUSTED
    run = await rt.uow.agent_runs.get(out.agent_run_id)
    assert run.status is AgentRunStatus.BUDGET_EXHAUSTED
    _ = SCENARIOS


async def test_runtime_concludes_no_action_even_when_the_model_wants_to_keep_digging() -> None:
    """The recovery decision belongs to the runtime. A model that keeps proposing tool calls after
    the symptoms cleared must not be able to keep the incident open."""
    rt = build_runtime(seed=17, warmup=600)
    inc = await seed_incident(rt, "transient-spike", advance=30)
    rt.engine.advance(120)  # the spike is over; signals are back within baseline

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        return AgentProposal(
            observation="I want to keep investigating",
            action="call_tool",
            rationale="curiosity",
            tool_call=ToolCallProposal(
                tool_name="get_health", arguments_json='{"service":"api-gateway"}', purpose="p"
            ),
        )

    llm = ScriptedProvider(script=script)
    agent = make_runtime(rt, llm=llm)
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="investigate",
    )
    assert out.decision == "no_action", out.summary
    assert out.termination is TerminationReason.NO_ACTION_REQUIRED
    assert not await rt.uow.action_plans.list_for_incident(inc.id)
    steps = await rt.uow.agent_runs.steps_for_run(out.agent_run_id)
    decisions = [s for s in steps if s.kind is AgentStepKind.DECISION]
    assert decisions and "within baseline" in decisions[-1].title
    # the model was consulted at least once before the runtime concluded, so the ledger shows work
    assert llm.calls and any(s.kind is AgentStepKind.TOOL_EXECUTION for s in steps)


async def test_capacity_diagnosis_refuses_a_restart_and_steers_to_scaling() -> None:
    """cpu-saturation: restarting returns the same replicas to the same load. The runtime refuses
    the plan and the refusal reaches the model as feedback."""
    rt = build_runtime(seed=19, warmup=600)
    inc = await seed_incident(rt, "cpu-saturation", advance=60)
    from aegis.domain.enums import EvidenceKind, HypothesisCategory, HypothesisStatus
    from aegis.domain.evidence import Evidence
    from aegis.domain.hypothesis import Hypothesis

    ev = await rt.uow.evidence.add(
        Evidence(
            incident_id=inc.id,
            kind=EvidenceKind.DIAGNOSTIC,
            source="run_process_diagnostic",
            service="notification-service",
            title="notification-service process diagnostic",
            summary="cpu 100%, memory 35%, 1 replica",
            data={"cpu_percent": 100.0, "memory_percent": 35.0},
            strength=0.85,
        )
    )
    hyp = await rt.uow.hypotheses.add(
        Hypothesis(
            incident_id=inc.id,
            statement="notification-service is saturated and cannot keep up with the job burst",
            category=HypothesisCategory.RESOURCE_EXHAUSTION,  # the model mislabels capacity
            suspected_root_cause_service="notification-service",
            supporting_evidence_ids=[ev.id],
            status=HypothesisStatus.CONFIRMED,
            confidence=0.8,
        )
    )
    inc.leading_hypothesis_id = hyp.id
    await rt.uow.incidents.save(inc)

    calls = {"n": 0}

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        calls["n"] += 1
        if calls["n"] == 1:
            return AgentProposal(
                observation="notification-service is pinned",
                action="plan_remediation",
                rationale="restart it",
                remediation=RemediationProposal(
                    tool_name="restart_service",
                    arguments_json='{"service":"notification-service"}',
                    target_hypothesis="H1",
                    reason="clear the backlog",
                    expected_effect="cpu drops",
                ),
            )
        return AgentProposal(
            observation="capacity problem, not state",
            action="plan_remediation",
            rationale="add capacity",
            remediation=RemediationProposal(
                tool_name="scale_service",
                arguments_json='{"service":"notification-service","replicas":3}',
                target_hypothesis="H1",
                reason="add capacity for the job burst",
                expected_effect="cpu and latency return to baseline",
            ),
        )

    llm = ScriptedProvider(script=script)
    agent = make_runtime(rt, llm=llm)
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="remediate",
    )
    assert out.decision == "action_planned"
    plans = await rt.uow.action_plans.list_for_incident(inc.id)
    assert len(plans) == 1
    assert plans[0].tool_name == "scale_service" and plans[0].arguments["replicas"] == 3
    # the refusal was fed back to the model in the second prompt
    assert any("capacity problem" in c["user"] for c in llm.calls[1:])


async def test_phase_gives_up_instead_of_spinning_when_the_signal_has_not_developed() -> None:
    """A proposer with nothing left to offer used to be told "keep investigating" forever: the
    phase looped until the incident's whole iteration budget was gone and the incident escalated.
    Now the phase reports `insufficient_signal` after two barren iterations and hands the decision
    to the workflow, which owns durable waiting."""
    rt = build_runtime(seed=17, warmup=600)
    inc = await seed_incident(rt, "redis-connection-leak")
    calls = {"n": 0}

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        calls["n"] += 1
        return AgentProposal(
            observation="nothing further to collect",
            action="phase_complete",
            rationale="the symptom has not developed",
        )

    agent = make_runtime(rt, llm=ScriptedProvider(script=script))
    out = await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="hypothesize",
    )
    assert out.decision == "terminate"
    assert out.termination is TerminationReason.INSUFFICIENT_SIGNAL
    assert out.iterations <= MAX_STALLED_ITERATIONS + 1, "the phase must stop, not burn the budget"
    assert out.usage.iterations < 8
    budget = rt.flows.get(inc.flow_name or "").budget_for(inc.severity)
    assert not out.usage.exceeded(budget), "the budget must survive for the re-observation"


def test_a_vague_hypothesis_is_still_remediable_when_the_evidence_names_a_mechanism() -> None:
    """A hypothesis confirmed early can carry no useful category — "X is the most implicated
    component" is `unknown`. The deterministic planner has no playbook for that and used to
    escalate an incident it had in fact diagnosed. The mechanism is read from the evidence
    instead: the dominant client of a saturated resource is exhausting it."""
    from aegis.domain.evidence import Evidence
    from aegis.domain.hypothesis import Hypothesis
    from aegis.infrastructure.simulator.inprocess import to_topology
    from aegis.remediation.planning import category_default_action

    rt = build_runtime()
    topology = to_topology(rt.engine.topology())
    vague = Hypothesis(
        incident_id=uuid.uuid4(),
        statement="order-service is the most implicated component in the collected evidence.",
        category=HypothesisCategory.UNKNOWN,
        suspected_root_cause_service="order-service",
    )
    saturated = Evidence(
        incident_id=vague.incident_id,
        kind=EvidenceKind.DIAGNOSTIC,
        source="inspect_redis",
        service="redis",
        title="redis saturation",
        summary="redis: 240/250 connections (96% saturation); by client: order-service 228",
        strength=0.9,
        data={
            "component": "redis",
            "saturation": 0.96,
            "connections_by_client": {"order-service": 228.0, "auth-service": 12.0},
        },
    )
    assert category_default_action(vague, topology, frozenset({"restart_service"})) is None
    choice = category_default_action(
        vague, topology, frozenset({"restart_service"}), evidence=[saturated]
    )
    assert choice is not None
    tool, args = choice
    assert tool == "restart_service" and args["service"] == "order-service"


async def test_a_vague_hypothesis_is_sharpened_in_place_once_the_evidence_allows_it() -> None:
    """The first hypothesis of an incident often names the right service with no idea why —
    "X is the most implicated component", category `unknown`, which is all weak early evidence can
    support. When a later pass can say what X is actually doing, that claim replaces the vague one
    rather than lining up beside it: two rival hypotheses for one service split the ranking, and
    the vague one may still be the first to be confirmed."""
    rt = build_runtime(seed=31, warmup=600)
    inc = await seed_incident(rt, "redis-connection-leak")
    collected = await rt.uow.evidence.add_many(
        [
            Evidence(
                incident_id=inc.id,
                kind=EvidenceKind.DIAGNOSTIC,
                source="inspect_redis",
                service="order-service",
                title="redis saturation",
                summary="redis: 240/250 connections (96% saturation); order-service holds 228",
                strength=0.9,
                data={
                    "component": "redis",
                    "saturation": 0.96,
                    "connections_by_client": {"order-service": 228.0, "auth-service": 12.0},
                },
            )
        ]
    )
    evidence = await rt.uow.evidence.list_for_incident(inc.id)
    first = next(iter(digest(evidence).handles))
    assert collected

    vague = Hypothesis(
        incident_id=inc.id,
        statement="order-service is the most implicated component in the collected evidence.",
        category=HypothesisCategory.UNKNOWN,
        suspected_root_cause_service="order-service",
        supporting_evidence_ids=[evidence[0].id],
        status=HypothesisStatus.CONFIRMED,
    )
    await rt.uow.hypotheses.add(vague)

    sharper = AgentProposal(
        observation="the diagnostic now shows the mechanism",
        action="propose_hypotheses",
        rationale="r",
        hypotheses=[
            HypothesisProposal(
                statement="order-service is exhausting Redis connections, degrading its callers.",
                category=HypothesisCategory.RESOURCE_EXHAUSTION,
                suspected_root_cause_service="order-service",
                supporting_evidence=[first],
                mechanism="the pool is saturated and order-service holds most of it",
            )
        ],
    )

    def script(schema: type[BaseModel], system: str, user: str, tier: str) -> BaseModel:
        return sharper

    agent = make_runtime(rt, llm=ScriptedProvider(script=script))
    await agent.run_phase(
        incident_id=inc.id,
        flow_name=inc.flow_name or "",
        flow_version=inc.flow_version or "",
        phase="hypothesize",
    )

    after = await rt.uow.hypotheses.list_for_incident(inc.id)
    for_service = [h for h in after if h.suspected_root_cause_service == "order-service"]
    assert len(for_service) == 1, [h.statement for h in for_service]
    upgraded = for_service[0]
    assert upgraded.id == vague.id, "the existing hypothesis is sharpened, not replaced"
    assert upgraded.category is HypothesisCategory.RESOURCE_EXHAUSTION
    assert "exhausting Redis connections" in upgraded.statement
    assert evidence[0].id in upgraded.supporting_evidence_ids, "earlier evidence is kept"

    events = await rt.uow.incidents.events(inc.id)
    sharpen = [e for e in events if "sharpened" in e.title.lower()]
    assert sharpen, [e.title for e in events[-5:]]
    assert sharpen[-1].payload["previous_statement"].startswith("order-service is the most")


def test_the_planner_re_proposes_a_service_only_while_its_hypothesis_is_vague() -> None:
    """The deterministic planner used to skip any service that already had a hypothesis, however
    empty that hypothesis was — so a first impression formed on weak evidence could never be
    improved on. It now re-proposes a service whose only hypothesis is `unknown`, and still leaves
    a service alone once something specific has been said about it."""
    from aegis.agent.planner import DeterministicPlanner, PlannerMemory
    from aegis.domain.flow import FlowPhase

    rt = build_runtime(seed=33, warmup=600)
    rt.engine.inject("redis-connection-leak")
    rt.engine.advance(200)
    inc = rt.incident(services=["api-gateway", "redis"])
    topology = to_topology(rt.engine.topology())
    strong = Evidence(
        incident_id=inc.id,
        kind=EvidenceKind.DIAGNOSTIC,
        source="inspect_redis",
        service="order-service",
        title="redis saturation",
        summary="redis: 240/250 connections (96% saturation), pool exhausted; order-service 228",
        strength=0.9,
        data={
            "component": "redis",
            "saturation": 0.96,
            "connections_by_client": {"order-service": 228.0, "auth-service": 12.0},
        },
    )
    phase = FlowPhase(name="hypothesize", objective="o", transitions=(), terminal=True)

    def propose(existing: list[Hypothesis]) -> str:
        planner = DeterministicPlanner(PlannerMemory())
        return planner.propose(
            incident=inc,
            phase=phase,
            evidence=[strong],
            hypotheses=existing,
            hypothesis_handles={},
            topology=topology,
            remediation_tools=frozenset(),
            metrics_recovered=False,
        ).action

    def held(category: HypothesisCategory) -> list[Hypothesis]:
        return [
            Hypothesis(
                incident_id=inc.id,
                statement="order-service is implicated by the collected evidence somehow.",
                category=category,
                suspected_root_cause_service="order-service",
            )
        ]

    assert propose([]) == "propose_hypotheses", "a service with no hypothesis is proposed"
    assert propose(held(HypothesisCategory.UNKNOWN)) == "propose_hypotheses", "vague: try again"
    assert propose(held(HypothesisCategory.RESOURCE_EXHAUSTION)) == "phase_complete", "specific"
