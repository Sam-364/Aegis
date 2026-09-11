"""The agent runtime.

    observe ─▶ (decide: transition/terminate?) ─▶ propose ─▶ {execute_tool | apply_hypotheses |
    plan_remediation | conclude} ─▶ observe …

* ``observe`` is deterministic: it loads state from the database, computes phase facts and lets the
  FlowRuntime decide whether the phase is over. It also enforces budgets and incident liveness.
* ``propose`` asks the model for exactly one structured step, or the deterministic planner when
  the model is unavailable or keeps producing invalid steps.
* Action nodes validate the proposal against the runtime's rules, execute through the authorized
  tool executor, and record every step. Feedback on refusals flows back into the next prompt.

The graph is checkpointed (thread id = agent run id) so a crashed worker resumes after the last
completed node instead of re-running tool calls.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from aegis.agent.events import emit
from aegis.agent.planner import DeterministicPlanner, PlannerMemory
from aegis.agent.prompts import (
    JUDGE_SYSTEM,
    SYSTEM_PROMPT,
    allowed_actions,
    build_judge_prompt,
    build_user_prompt,
    hypotheses_digest,
)
from aegis.agent.schemas import (
    AgentProposal,
    HypothesisUpdateProposal,
    RemediationProposal,
    TestJudgement,
    ToolCallProposal,
    parse_arguments,
)
from aegis.agent.state import AgentState, initial_state
from aegis.domain.action import ActionPlan, RollbackPlan
from aegis.domain.agent import AgentRun, AgentStep
from aegis.domain.base import Actor
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import (
    ActionPlanStatus,
    AgentRunStatus,
    AgentStepKind,
    Environment,
    ExecutionStatus,
    HypothesisStatus,
    TerminationReason,
    ToolCategory,
)
from aegis.domain.errors import AegisError, InvalidToolArguments, LLMError
from aegis.domain.events import EventType
from aegis.domain.evidence import Evidence
from aegis.domain.flow import BudgetUsage, ExecutionBudget, FlowPack, FlowPhase
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.ids import AgentRunId, IncidentId
from aegis.domain.incident import Incident
from aegis.domain.telemetry import Topology
from aegis.domain.tool import ToolCallRequest, ToolExecutionRecord
from aegis.evidence.service import EvidenceService, digest
from aegis.flow.registry import FlowRegistry
from aegis.flow.runtime import FlowRuntime, PhaseFacts
from aegis.hypotheses.engine import HypothesisEngine, HypothesisProposal, ProposalRejected
from aegis.logging import get_logger
from aegis.ports.llm import LLMProvider
from aegis.ports.messaging import EventPublisher
from aegis.ports.repositories import UnitOfWork, UnitOfWorkFactory
from aegis.ports.telemetry import InfrastructureGateway, TelemetryProvider
from aegis.remediation.planning import (
    RemediationMismatch,
    TargetMismatch,
    build_verification_spec,
    default_rollback,
    risk_for,
    validate_remediation_fit,
    validate_remediation_target,
)
from aegis.telemetry.metrics import AGENT_ITERATIONS, AGENT_RUNS_TOTAL
from aegis.tools.authorizer import ToolAuthorizer
from aegis.tools.context import MemorySearch, ToolContext
from aegis.tools.executor import ToolExecutor
from aegis.tools.registry import ToolRegistry
from aegis.verification.engine import VerificationEngine

log = get_logger(__name__)


async def _no_sleep(_seconds: float) -> None:
    """evaluate_once never waits; the engine only sleeps inside verify()."""
    return None


Decision = Literal["transition", "terminate", "action_planned", "no_action", "escalate"]


@dataclass
class AgentHooks:
    on_step: Callable[[str], Awaitable[None]] | None = None
    should_cancel: Callable[[], bool] | None = None


@dataclass
class AgentDependencies:
    uow_factory: UnitOfWorkFactory
    registry: ToolRegistry
    authorizer: ToolAuthorizer
    flows: FlowRegistry
    telemetry: TelemetryProvider
    infrastructure: InfrastructureGateway
    environment: Environment
    llm: LLMProvider | None = None
    memory_search: MemorySearch | None = None
    publisher: EventPublisher | None = None
    clock: Clock | None = None
    hypothesis_engine: HypothesisEngine | None = None
    checkpointer: BaseCheckpointSaver[Any] | None = None
    hooks: AgentHooks | None = None
    min_remediation_confidence: float = 0.55
    max_invalid_streak: int = 2


MAX_STALLED_ITERATIONS = 2
"""Consecutive `phase_complete` proposals with no new evidence before the phase gives up."""


class PhaseOutcome(BaseModel):
    agent_run_id: uuid.UUID
    incident_id: uuid.UUID
    phase: str
    decision: str
    trigger: str | None = None
    next_phase: str | None = None
    termination: TerminationReason | None = None
    action_plan_id: uuid.UUID | None = None
    iterations: int = 0
    usage: BudgetUsage
    llm_used: bool = True
    steps: int = 0
    summary: str = ""


class AgentRuntime:
    def __init__(self, deps: AgentDependencies) -> None:
        self.deps = deps
        self.clock = deps.clock or SystemClock()
        self.engine = deps.hypothesis_engine or HypothesisEngine()
        self.checkpointer = deps.checkpointer or InMemorySaver()
        self._planners: dict[str, DeterministicPlanner] = {}
        self._flow_cache: dict[tuple[str, str], FlowPack] = {}
        self._topology: Topology | None = None
        self._topology_at: float = 0.0
        self._memory_hints: dict[str, str] = {}
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ public -----------------

    async def run_phase(
        self,
        *,
        incident_id: uuid.UUID,
        flow_name: str,
        flow_version: str,
        phase: str,
        agent_run_id: uuid.UUID | None = None,
        usage: BudgetUsage | None = None,
        workflow_id: str | None = None,
        attempt: int = 1,
        initial_feedback: list[str] | None = None,
    ) -> PhaseOutcome:
        run_id = agent_run_id or uuid.uuid4()
        flow = self._flow(flow_name, flow_version)
        config = {"configurable": {"thread_id": str(run_id)}, "recursion_limit": 400}
        existing = await self.graph.aget_state(config)
        resuming = bool(existing and existing.values)
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            budget = flow.budget_for(incident.severity)
            run = await self._load_or_create_run(
                uow,
                incident,
                flow,
                phase,
                run_id,
                usage or BudgetUsage(),
                budget,
                workflow_id,
                attempt,
            )
            if not resuming:
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.AGENT_RUN_STARTED,
                    actor=Actor.agent(str(run_id)),
                    title=f"Agent started phase '{phase}'",
                    payload={
                        "agent_run_id": str(run_id),
                        "phase": phase,
                        "flow": flow.ref,
                        "budget": budget.model_dump(),
                    },
                )
            await uow.commit()
        llm_available = self.deps.llm is not None and await self.deps.llm.healthy()
        state: AgentState | None = (
            None
            if resuming
            else initial_state(
                incident_id=str(incident_id),
                agent_run_id=str(run_id),
                flow_name=flow_name,
                flow_version=flow_version,
                phase=phase,
                usage=(usage or BudgetUsage()).model_dump(),
                llm_available=llm_available,
                feedback=list(initial_feedback or []),
                ticked_at=self.clock.now().isoformat(),
            )
        )
        if resuming:
            log.info("agent.resume", agent_run_id=str(run_id), phase=phase)
        final: AgentState = await self.graph.ainvoke(state, config=config)
        return await self._finish(run, final, flow)

    # ------------------------------------------------------------------ graph ------------------

    def _build_graph(self) -> Any:
        g: StateGraph[AgentState] = StateGraph(AgentState)
        g.add_node("observe", self._observe)
        g.add_node("propose", self._propose)
        g.add_node("execute_tool", self._execute_tool)
        g.add_node("apply_hypotheses", self._apply_hypotheses)
        g.add_node("plan_remediation", self._plan_remediation)
        g.add_node("conclude", self._conclude)
        g.add_edge(START, "observe")
        g.add_conditional_edges(
            "observe", self._route_after_observe, {"propose": "propose", END: END}
        )
        g.add_conditional_edges(
            "propose",
            self._route_after_propose,
            {
                "execute_tool": "execute_tool",
                "apply_hypotheses": "apply_hypotheses",
                "plan_remediation": "plan_remediation",
                "conclude": "conclude",
                "observe": "observe",
            },
        )
        for node in ("execute_tool", "apply_hypotheses", "plan_remediation", "conclude"):
            g.add_conditional_edges(node, self._route_loop, {"observe": "observe", END: END})
        return g.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _route_after_observe(state: AgentState) -> str:
        return END if state.get("decision") else "propose"

    @staticmethod
    def _route_after_propose(state: AgentState) -> str:
        proposal = state.get("last_proposal")
        if not proposal:
            return "observe"
        return {
            "call_tool": "execute_tool",
            "propose_hypotheses": "apply_hypotheses",
            "update_hypothesis": "apply_hypotheses",
            "plan_remediation": "plan_remediation",
        }.get(proposal["action"], "conclude")

    @staticmethod
    def _route_loop(state: AgentState) -> str:
        return END if state.get("decision") else "observe"

    # ------------------------------------------------------------------ nodes ------------------

    async def _observe(self, state: AgentState) -> AgentState:
        await self._hook("observe")
        flow, phase = self._phase(state)
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            usage = BudgetUsage(**state["usage"])
            budget = flow.budget_for(incident.severity)
            if not incident.status.is_active:
                return {
                    **state,
                    "decision": "terminate",
                    "termination": TerminationReason.INCIDENT_INACTIVE.value,
                    "summary": f"incident is {incident.status.value}",
                }
            if (
                self.deps.hooks
                and self.deps.hooks.should_cancel
                and self.deps.hooks.should_cancel()
            ):
                return {
                    **state,
                    "decision": "terminate",
                    "termination": TerminationReason.ERROR.value,
                    "summary": "cancelled",
                }
            usage, state = self._tick(state, usage)
            exceeded = usage.exceeded(budget)
            if exceeded:
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.BUDGET_EXHAUSTED,
                    actor=Actor.agent(state["agent_run_id"]),
                    title="Execution budget exhausted",
                    payload={"reasons": exceeded},
                )
                await uow.commit()
                return {
                    **state,
                    "decision": "terminate",
                    "termination": TerminationReason.BUDGET_EXHAUSTED.value,
                    "summary": "; ".join(exceeded),
                }
            # Deterministic recovery check: the runtime, not the model, decides that symptoms
            # are gone. Computed once per iteration and reused by the proposal prompt.
            recovered, metric_lines = await self._signal_status(incident)
            state = {**state, "recovered": recovered, "metric_lines": metric_lines}
            if (
                recovered
                and state["iteration"] >= 1
                and not phase.plans_remediation
                and state.get("action_plan_id") is None
                and incident.active_action_plan_id is None
                and incident.remediation_attempts == 0
            ):
                await self._record_step(
                    uow,
                    state,
                    AgentStepKind.DECISION,
                    "observe",
                    "All detection signals returned within baseline; no remediation required",
                    {},
                    {"metrics": metric_lines},
                )
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.AGENT_STEP,
                    actor=Actor.agent(state["agent_run_id"]),
                    title=(
                        "Runtime: all detection signals returned within baseline "
                        "without remediation"
                    ),
                    payload={"action": "conclude_no_action", "metrics": metric_lines},
                )
                await uow.commit()
                return {
                    **state,
                    "decision": "no_action",
                    "summary": "detection signals returned within baseline without remediation",
                }
            evidence = await uow.evidence.list_for_incident(incident.id)
            hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
            facts = self._facts(state, evidence, hypotheses)
            trigger, nxt = FlowRuntime(flow).decide(phase, facts)
            if nxt is not None:
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.PHASE_EXITED,
                    actor=Actor.agent(state["agent_run_id"]),
                    title=f"Phase '{phase.name}' complete ({trigger}) → '{nxt}'",
                    payload={
                        "phase": phase.name,
                        "trigger": trigger,
                        "next_phase": nxt,
                        "facts": facts.__dict__,
                    },
                )
                await uow.commit()
                return {
                    **state,
                    "decision": "transition",
                    "trigger": trigger,
                    "next_phase": nxt,
                    "summary": f"{trigger} → {nxt}",
                }
        return {**state, "decision": None}

    MAX_TICK_SECONDS = 120.0

    def _tick(self, state: AgentState, usage: BudgetUsage) -> tuple[BudgetUsage, AgentState]:
        """Charge wall-clock time to the runtime budget, which `ExecutionBudget` declares and
        nothing was previously incrementing. A single tick is capped: after a worker crash the gap
        between two observations is downtime, not agent work, and a resumed phase must not
        immediately exhaust its budget because of it."""
        now = self.clock.now()
        previous = state.get("ticked_at")
        elapsed = 0.0
        if previous:
            try:
                elapsed = max(0.0, (now - datetime.fromisoformat(previous)).total_seconds())
            except ValueError:
                elapsed = 0.0
        charged = min(elapsed, self.MAX_TICK_SECONDS)
        usage = usage.add(runtime_seconds=charged)
        return usage, {**state, "usage": usage.model_dump(), "ticked_at": now.isoformat()}

    async def _propose(self, state: AgentState) -> AgentState:
        await self._hook("propose")
        flow, phase = self._phase(state)
        run_id = state["agent_run_id"]
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            evidence = await uow.evidence.list_for_incident(incident.id)
            hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
            topology = await self._topo()
            usage = BudgetUsage(**state["usage"])
            budget = flow.budget_for(incident.severity)
            recovered = bool(state.get("recovered", False))
            metric_lines = list(state.get("metric_lines", []))
            hyp_text, hyp_handles = hypotheses_digest(hypotheses)
            ev_digest = digest(evidence)
            proposal: AgentProposal | None = None
            model = ""
            tokens_in = tokens_out = 0
            latency_ms = 0.0
            llm_available = state["llm_available"] and self.deps.llm is not None
            use_fallback = (not llm_available) or state[
                "invalid_streak"
            ] >= self.deps.max_invalid_streak
            feedback = list(state["feedback"])
            if not use_fallback and self.deps.llm is not None:
                prompt = build_user_prompt(
                    incident=incident,
                    flow=flow,
                    phase=phase,
                    topology=topology,
                    evidence_text=ev_digest.text(),
                    hypotheses_text=hyp_text,
                    tools=self.deps.registry.descriptions_for(phase.allowed_tools),
                    feedback=feedback,
                    budget=budget,
                    usage=usage,
                    iteration=state["iteration"],
                    now=self.clock.now(),
                    remediation_tools=sorted(flow.remediation_tools),
                    recent_metrics="\n".join(metric_lines),
                    signals_recovered=recovered,
                    memory_text=await self._memory_text(incident),
                )
                started = time.perf_counter()
                try:
                    result = await self.deps.llm.complete_structured(
                        schema=AgentProposal,
                        system=SYSTEM_PROMPT,
                        user=prompt,
                        tier="reasoner",
                        cache_key=str(incident.id),
                    )
                    proposal = result.value
                    model = result.usage.model
                    tokens_in, tokens_out = result.usage.input_tokens, result.usage.output_tokens
                    latency_ms = result.usage.latency_ms
                    usage = usage.add(llm_calls=1, llm_tokens=result.usage.total_tokens)
                except LLMError as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    llm_available = False
                    await emit(
                        uow.incidents,
                        self.deps.publisher,
                        incident_id=incident.id,
                        type=EventType.LLM_FALLBACK,
                        actor=Actor.agent(run_id),
                        title="LLM unavailable; continuing with deterministic diagnostics",
                        payload={"error": str(exc)[:300]},
                    )
                    log.warning("agent.llm_fallback", error=str(exc))
            if proposal is not None and proposal.action not in allowed_actions(phase):
                feedback.append(
                    f"action '{proposal.action}' is not allowed in phase '{phase.name}'; "
                    f"allowed: {', '.join(allowed_actions(phase))}"
                )
                proposal = None
                state = {**state, "invalid_streak": state["invalid_streak"] + 1}
                if state["invalid_streak"] < self.deps.max_invalid_streak:
                    await self._record_step(
                        uow,
                        state,
                        AgentStepKind.FALLBACK,
                        "propose",
                        "Invalid proposal rejected",
                        {},
                        {"feedback": feedback[-1]},
                    )
                    await uow.commit()
                    return {
                        **state,
                        "feedback": feedback[-12:],
                        "usage": usage.model_dump(),
                        "last_proposal": None,
                        "iteration": state["iteration"] + 1,
                    }
            if proposal is None:
                planner = self._planners.setdefault(run_id, DeterministicPlanner(PlannerMemory()))
                proposal = planner.propose(
                    incident=incident,
                    phase=phase,
                    topology=topology,
                    evidence=evidence,
                    hypotheses=hypotheses,
                    hypothesis_handles=hyp_handles,
                    remediation_tools=flow.remediation_tools,
                    metrics_recovered=recovered,
                )
                model = "deterministic-planner"
                kind = AgentStepKind.FALLBACK
            else:
                kind = AgentStepKind.PROPOSAL
            step = await self._record_step(
                uow,
                state,
                kind,
                "propose",
                f"{proposal.action}: {proposal.observation[:120]}",
                {
                    "phase": phase.name,
                    "iteration": state["iteration"],
                    "evidence": len(evidence),
                    "hypotheses": len(hypotheses),
                },
                {"proposal": proposal.model_dump(mode="json"), "recovered": recovered},
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
            )
            await emit(
                uow.incidents,
                self.deps.publisher,
                incident_id=incident.id,
                type=EventType.AGENT_STEP,
                actor=Actor.agent(run_id),
                title=f"[{phase.name}] {proposal.action}: {proposal.observation[:140]}",
                payload={
                    "step_id": str(step.id),
                    "kind": kind.value,
                    "action": proposal.action,
                    "observation": proposal.observation,
                    "rationale": proposal.rationale,
                    "model": model,
                    "tokens": tokens_in + tokens_out,
                },
            )
            await uow.commit()
        return {
            **state,
            "last_proposal": proposal.model_dump(mode="json"),
            "usage": usage.model_dump(),
            "llm_available": llm_available,
            "model": model or state.get("model", ""),
            "feedback": feedback[-12:],
            "step_seq": state["step_seq"] + 1,
        }

    async def _execute_tool(self, state: AgentState) -> AgentState:
        await self._hook("execute_tool")
        flow, phase = self._phase(state)
        proposal = AgentProposal.model_validate(state["last_proposal"])
        call = proposal.tool_call
        run_id = state["agent_run_id"]
        feedback = list(state["feedback"])
        usage = BudgetUsage(**state["usage"])
        if call is None:
            feedback.append("action call_tool requires a tool_call")
            return self._next(state, feedback, usage, invalid=True)
        try:
            arguments = parse_arguments(call.arguments_json)
        except ValueError as exc:
            feedback.append(f"{call.tool_name}: {exc}")
            return self._next(state, feedback, usage, invalid=True)
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            topology = await self._topo()
            ctx = self._tool_context(incident, flow, phase, run_id, topology)
            request = ToolCallRequest(
                incident_id=incident.id,
                tool_name=call.tool_name,
                arguments=arguments,
                rationale=call.purpose,
                requested_by=Actor.agent(run_id),
                agent_run_id=uuid.UUID(run_id),
                phase=phase.name,
                requested_at=self.clock.now(),
            )
            executor = ToolExecutor(
                self.deps.registry,
                self.deps.authorizer,
                ledger=uow.tool_executions,
                evidence=uow.evidence,
                audit=uow.audit,
                clock=self.clock,
            )
            record = await executor.execute(
                request,
                ctx=ctx,
                budget=flow.budget_for(incident.severity),
                usage=usage,
                in_agent_loop=True,
            )
            await self._record_step(
                uow,
                state,
                AgentStepKind.AUTHORIZATION,
                "execute_tool",
                f"{'allowed' if record.authorization.allowed else 'denied'}: {call.tool_name}",
                {"tool": call.tool_name, "arguments": arguments},
                {"decision": record.authorization.model_dump(mode="json")},
                tool_execution_id=record.id,
            )
            invalid = False
            if record.status is ExecutionStatus.DENIED:
                feedback.append(
                    f"{call.tool_name} was refused ({record.authorization.denial_code}): "
                    f"{record.authorization.reason}. Allowed tools now: "
                    f"{', '.join(sorted(phase.allowed_tools))}"
                )
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.TOOL_DENIED,
                    actor=Actor.agent(run_id),
                    title=f"Refused {call.tool_name}: {record.authorization.denial_code}",
                    payload={
                        "tool": call.tool_name,
                        "arguments": arguments,
                        "denial_code": record.authorization.denial_code,
                        "reason": record.authorization.reason,
                        "checks": [c.model_dump() for c in record.authorization.checks],
                        "tool_execution_id": str(record.id),
                    },
                )
                invalid = True
            else:
                usage = usage.add(tool_calls=1)
                produced = [await uow.evidence.get(eid) for eid in record.evidence_ids]
                await EvidenceService(uow.evidence).link_evidence_to_services(produced)
                title = (
                    f"{call.tool_name}: {record.result_summary[:160]}"
                    if record.succeeded
                    else f"{call.tool_name} failed: {record.error}"
                )
                await self._record_step(
                    uow,
                    state,
                    AgentStepKind.TOOL_EXECUTION,
                    "execute_tool",
                    title,
                    {"tool": call.tool_name, "arguments": arguments},
                    {
                        "status": record.status.value,
                        "summary": record.result_summary,
                        "evidence_ids": [str(e) for e in record.evidence_ids],
                        "duration_ms": record.duration_ms,
                    },
                    tool_execution_id=record.id,
                    latency_ms=record.duration_ms or 0,
                )
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.TOOL_EXECUTED if record.succeeded else EventType.TOOL_FAILED,
                    actor=Actor.agent(run_id),
                    title=title,
                    payload={
                        "tool": call.tool_name,
                        "arguments": arguments,
                        "status": record.status.value,
                        "summary": record.result_summary,
                        "tool_execution_id": str(record.id),
                        "evidence_ids": [str(e) for e in record.evidence_ids],
                        "duration_ms": record.duration_ms,
                    },
                )
                if produced:
                    await emit(
                        uow.incidents,
                        self.deps.publisher,
                        incident_id=incident.id,
                        type=EventType.EVIDENCE_COLLECTED,
                        actor=Actor.agent(run_id),
                        title=f"{len(produced)} evidence item(s) from {call.tool_name}",
                        payload={
                            "evidence": [
                                {
                                    "id": str(e.id),
                                    "kind": e.kind.value,
                                    "title": e.title,
                                    "service": e.service,
                                    "strength": e.strength,
                                }
                                for e in produced
                            ]
                        },
                    )
                if not record.succeeded:
                    feedback.append(f"{call.tool_name} failed: {record.error}")
                if call.tests_hypothesis and record.succeeded:
                    usage = await self._judge(uow, state, incident, call, produced, usage, topology)
            await uow.commit()
        return self._next(state, feedback, usage, invalid=invalid)

    async def _apply_hypotheses(self, state: AgentState) -> AgentState:
        await self._hook("apply_hypotheses")
        _flow, _phase = self._phase(state)
        proposal = AgentProposal.model_validate(state["last_proposal"])
        run_id = state["agent_run_id"]
        feedback = list(state["feedback"])
        usage = BudgetUsage(**state["usage"])
        invalid = False
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            evidence = await uow.evidence.list_for_incident(incident.id)
            handles = digest(evidence).handles
            topology = await self._topo()
            known = frozenset(n.name for n in topology.nodes)
            hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
            _text, hyp_handles = hypotheses_digest(hypotheses)
            created: list[Hypothesis] = []
            if proposal.action == "propose_hypotheses":
                if not proposal.hypotheses:
                    feedback.append("propose_hypotheses requires at least one hypothesis")
                    invalid = True
                for hp in proposal.hypotheses[:4]:
                    try:
                        h = self.engine.accept(
                            hp,
                            incident=incident,
                            resolve=handles,
                            known_services=known,
                            agent_run_id=uuid.UUID(run_id),
                            proposed_by=(
                                "deterministic"
                                if state.get("model") == "deterministic-planner"
                                else "llm"
                            ),
                        )
                    except ProposalRejected as exc:
                        feedback.append(f"hypothesis rejected: {exc}")
                        invalid = True
                        continue
                    dup = next(
                        (
                            x
                            for x in hypotheses
                            if x.suspected_root_cause_service == h.suspected_root_cause_service
                            and x.category == h.category
                        ),
                        None,
                    )
                    if dup is not None:
                        for eid in h.supporting_evidence_ids:
                            if eid not in dup.supporting_evidence_ids:
                                dup.supporting_evidence_ids.append(eid)
                        feedback.append(
                            f"hypothesis about {h.suspected_root_cause_service} already exists; "
                            f"its evidence was merged"
                        )
                        continue
                    await uow.hypotheses.add(h)
                    hypotheses.append(h)
                    created.append(h)
            elif proposal.action == "update_hypothesis" and proposal.hypothesis_update is not None:
                invalid = not await self._apply_update(
                    uow, state, incident, proposal.hypothesis_update, hyp_handles, handles, feedback
                )
            evidence_map = {e.id: e for e in evidence}
            now = self.clock.now()
            for h in hypotheses:
                self.engine.rescore(
                    h, evidence=evidence_map, incident=incident, topology=topology, now=now
                )
                await uow.hypotheses.save(h)
                await EvidenceService(uow.evidence).link_hypothesis(h)
            ranked = self.engine.rank(hypotheses)
            if ranked:
                incident.leading_hypothesis_id = ranked[0].id
                incident.root_cause_summary = ranked[0].statement
                await uow.incidents.save(incident)
            for h in created:
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.HYPOTHESIS_CREATED,
                    actor=Actor.agent(run_id),
                    title=f"Hypothesis: {h.statement[:140]} (confidence {h.confidence:.0%})",
                    payload=self._hypothesis_payload(h),
                )
            await self._record_step(
                uow,
                state,
                AgentStepKind.HYPOTHESIS_UPDATE,
                "apply_hypotheses",
                f"{len(created)} new, {len(hypotheses)} total; leading confidence "
                f"{ranked[0].confidence:.2f}"
                if ranked
                else "no hypotheses",
                {"action": proposal.action},
                {"hypotheses": [self._hypothesis_payload(h) for h in ranked[:5]]},
            )
            if ranked and not created and proposal.action == "update_hypothesis":
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.HYPOTHESIS_UPDATED,
                    actor=Actor.agent(run_id),
                    title=f"Hypothesis updated: {ranked[0].statement[:120]} "
                    f"(confidence {ranked[0].confidence:.0%})",
                    payload=self._hypothesis_payload(ranked[0]),
                )
            await uow.commit()
        return self._next(state, feedback, usage, invalid=invalid)

    async def _plan_remediation(self, state: AgentState) -> AgentState:
        await self._hook("plan_remediation")
        flow, _phase = self._phase(state)
        proposal = AgentProposal.model_validate(state["last_proposal"])
        rem = proposal.remediation
        run_id = state["agent_run_id"]
        feedback = list(state["feedback"])
        usage = BudgetUsage(**state["usage"])
        if rem is None:
            feedback.append("plan_remediation requires a remediation object")
            return self._next(state, feedback, usage, invalid=True)
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
            _text, hyp_handles = hypotheses_digest(hypotheses)
            topology = await self._topo()
            target = hyp_handles.get(rem.target_hypothesis)
            if target is None:
                feedback.append(f"unknown hypothesis handle {rem.target_hypothesis}")
                return self._next(state, feedback, usage, invalid=True)
            if (
                target.status not in (HypothesisStatus.CONFIRMED, HypothesisStatus.SUPPORTED)
                or target.confidence < self.deps.min_remediation_confidence
            ):
                feedback.append(
                    f"{rem.target_hypothesis} is {target.status.value} with confidence "
                    f"{target.confidence:.2f}; remediation requires a confirmed/supported "
                    f"hypothesis "
                    f"with confidence >= {self.deps.min_remediation_confidence:.2f}"
                )
                return self._next(state, feedback, usage, invalid=True)
            if rem.tool_name not in flow.remediation_tools or not self.deps.registry.has(
                rem.tool_name
            ):
                feedback.append(
                    f"{rem.tool_name} is not a remediation tool of this flow; choose from "
                    f"{', '.join(sorted(flow.remediation_tools))}"
                )
                return self._next(state, feedback, usage, invalid=True)
            defn = self.deps.registry.get(rem.tool_name)
            if defn.spec.category is not ToolCategory.MUTATING:
                feedback.append(f"{rem.tool_name} is not a remediation (mutating) tool")
                return self._next(state, feedback, usage, invalid=True)
            known = frozenset(n.name for n in topology.nodes)
            try:
                arguments = parse_arguments(rem.arguments_json)
                defn.parse_args(arguments, known_components=known)
                target_service_name = validate_remediation_target(
                    rem.tool_name, arguments, target, topology
                )
                evidence_items = await uow.evidence.list_for_incident(incident.id)
                validate_remediation_fit(rem.tool_name, target, evidence_items, target_service_name)
            except (ValueError, InvalidToolArguments, TargetMismatch, RemediationMismatch) as exc:
                feedback.append(f"remediation rejected: {exc}")
                return self._next(state, feedback, usage, invalid=True)
            target_service = str(arguments.get("service") or arguments.get("component") or "")
            baselines: dict[tuple[str, str], float | None] = {}
            for s in incident.signals:
                baselines[(s.service, s.metric)] = await self.deps.telemetry.baseline(
                    s.service, s.metric
                )
            for metric in defn.spec.verification_metrics:
                baselines[(target_service, metric)] = await self.deps.telemetry.baseline(
                    target_service, metric
                )
            spec = build_verification_spec(
                incident, baselines=baselines, target_service=target_service, tool_spec=defn.spec
            )
            rollback = self._rollback_plan(rem, flow, known)
            plan = ActionPlan(
                incident_id=incident.id,
                hypothesis_id=target.id,
                tool_name=rem.tool_name,
                tool_version=defn.spec.version,
                arguments=arguments,
                reason=rem.reason,
                expected_effect=rem.expected_effect,
                risk=risk_for(defn.spec, target, incident),
                rollback=rollback,
                verification=spec,
                timeout_seconds=int(defn.spec.timeout_seconds) + 60,
                status=ActionPlanStatus.PROPOSED,
                proposed_by="deterministic"
                if state.get("model") == "deterministic-planner"
                else "llm",
                agent_run_id=uuid.UUID(run_id),
                attempt=incident.remediation_attempts + 1,
            )
            await uow.action_plans.add(plan)
            incident.active_action_plan_id = plan.id
            await uow.incidents.save(incident)
            await EvidenceService(uow.evidence).link_action(incident.id, plan.id, target.id)
            await self._record_step(
                uow,
                state,
                AgentStepKind.REMEDIATION_PLAN,
                "plan_remediation",
                f"Proposed {rem.tool_name}({json.dumps(arguments)}) for {rem.target_hypothesis}",
                {"remediation": rem.model_dump()},
                {"action_plan": plan.model_dump(mode="json")},
            )
            await emit(
                uow.incidents,
                self.deps.publisher,
                incident_id=incident.id,
                type=EventType.REMEDIATION_PROPOSED,
                actor=Actor.agent(run_id),
                title=f"Remediation proposed: {rem.tool_name} on {target_service}",
                payload={
                    "action_plan_id": str(plan.id),
                    "tool": rem.tool_name,
                    "arguments": arguments,
                    "risk": plan.risk.value,
                    "reason": rem.reason,
                    "expected_effect": rem.expected_effect,
                    "hypothesis_id": str(target.id),
                    "hypothesis": target.statement,
                    "confidence": target.confidence,
                    "rollback": rollback.model_dump(mode="json"),
                    "verification": spec.model_dump(mode="json"),
                },
            )
            await uow.commit()
        return {
            **state,
            "decision": "action_planned",
            "action_plan_id": str(plan.id),
            "feedback": feedback[-12:],
            "usage": usage.model_dump(),
            "iteration": state["iteration"] + 1,
            "invalid_streak": 0,
            "summary": f"planned {rem.tool_name} on {target_service}",
        }

    async def _conclude(self, state: AgentState) -> AgentState:
        await self._hook("conclude")
        flow, phase = self._phase(state)
        proposal = AgentProposal.model_validate(state["last_proposal"])
        feedback = list(state["feedback"])
        usage = BudgetUsage(**state["usage"])
        run_id = state["agent_run_id"]
        async with self.deps.uow_factory() as uow:
            incident = await uow.incidents.get(uuid.UUID(state["incident_id"]))
            if proposal.action == "conclude_no_action":
                recovered, lines = await self._signal_status(incident)
                if not recovered:
                    feedback.append(
                        "cannot conclude 'no action': metrics are still anomalous: "
                        + "; ".join(lines[:4])
                    )
                    await self._record_step(
                        uow,
                        state,
                        AgentStepKind.DECISION,
                        "conclude",
                        "no-action conclusion rejected",
                        {},
                        {"metrics": lines},
                    )
                    await uow.commit()
                    return self._next(state, feedback, usage, invalid=True)
                await self._record_step(
                    uow,
                    state,
                    AgentStepKind.DECISION,
                    "conclude",
                    "No remediation required: metrics within baseline",
                    {},
                    {"metrics": lines},
                )
                await uow.commit()
                return {
                    **state,
                    "decision": "no_action",
                    "feedback": feedback,
                    "usage": usage.model_dump(),
                    "summary": "metrics recovered without action",
                }
            if proposal.action == "escalate":
                await self._record_step(
                    uow,
                    state,
                    AgentStepKind.DECISION,
                    "conclude",
                    f"Escalate: {proposal.rationale[:160]}",
                    {},
                    {"observation": proposal.observation},
                )
                await emit(
                    uow.incidents,
                    self.deps.publisher,
                    incident_id=incident.id,
                    type=EventType.AGENT_STEP,
                    actor=Actor.agent(run_id),
                    title=f"Agent requests escalation: {proposal.rationale[:140]}",
                    payload={"action": "escalate", "observation": proposal.observation},
                )
                await uow.commit()
                return {
                    **state,
                    "decision": "escalate",
                    "feedback": feedback,
                    "usage": usage.model_dump(),
                    "summary": proposal.rationale[:200],
                }
            # phase_complete: advisory; the FlowRuntime decides on the next observe.
            evidence = await uow.evidence.list_for_incident(incident.id)
            hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
            facts = self._facts(
                {**state, "iteration": state["iteration"] + 1}, evidence, hypotheses
            )
            evaluation = FlowRuntime(flow).evaluate_exit(phase, facts)
            if not evaluation.met:
                # The proposer has nothing left to offer but the phase is not finished. Whether
                # to loop or to wait depends on one question: is there anything left to try with
                # the data already in hand?
                #
                # There is, if a live hypothesis has never been tested — the flow's declared
                # `exhausted → hypothesize → validate` path will reach it, and stealing that
                # transition would strand an incident one step from its remediation. There is
                # also, if one is already confirmed: that belongs in `remediate`.
                #
                # There is not, if every live hypothesis has been tried and none stuck (or there
                # are none at all) while the evidence set is unchanged. Then the symptom simply
                # has not developed far enough to diagnose, and looping only spends the budget.
                # That is the workflow's problem, because durable waiting is what it owns.
                live = [
                    h
                    for h in hypotheses
                    if h.status not in (HypothesisStatus.REFUTED, HypothesisStatus.ABANDONED)
                ]
                nothing_left_to_try = not any(
                    not h.tests
                    or h.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.SUPPORTED)
                    for h in live
                )
                stalled = (
                    state.get("stalled_streak", 0) + 1
                    if len(evidence) == state.get("evidence_seen", -1)
                    else 0
                )
                state = {**state, "stalled_streak": stalled, "evidence_seen": len(evidence)}
                if stalled >= MAX_STALLED_ITERATIONS and nothing_left_to_try:
                    await self._record_step(
                        uow,
                        state,
                        AgentStepKind.DECISION,
                        "conclude",
                        "no new evidence and exit conditions unmet; the signal has not developed",
                        {},
                        {"unsatisfied": evaluation.unsatisfied, "evidence": len(evidence)},
                    )
                    await uow.commit()
                    return {
                        **state,
                        "decision": "terminate",
                        "termination": TerminationReason.INSUFFICIENT_SIGNAL.value,
                        "summary": (
                            "no new evidence in "
                            f"{MAX_STALLED_ITERATIONS} iterations; unmet: "
                            + ", ".join(evaluation.unsatisfied)
                        )[:200],
                    }
                feedback.append(
                    "phase exit conditions not yet met: "
                    + ", ".join(evaluation.unsatisfied)
                    + " — keep investigating"
                )
            else:
                state = {**state, "stalled_streak": 0, "evidence_seen": len(evidence)}
            await self._record_step(
                uow,
                state,
                AgentStepKind.DECISION,
                "conclude",
                "phase_complete requested",
                {},
                {"exit_met": evaluation.met, "unsatisfied": evaluation.unsatisfied},
            )
            await uow.commit()
        return self._next(state, feedback, usage, invalid=not evaluation.met)

    # ------------------------------------------------------------------ helpers ----------------

    def _next(
        self, state: AgentState, feedback: list[str], usage: BudgetUsage, *, invalid: bool
    ) -> AgentState:
        return {
            **state,
            "feedback": feedback[-12:],
            "usage": usage.add(iterations=1).model_dump(),
            "iteration": state["iteration"] + 1,
            "last_proposal": None,
            "invalid_streak": state["invalid_streak"] + 1 if invalid else 0,
            "decision": None,
        }

    async def _hook(self, node: str) -> None:
        if self.deps.hooks and self.deps.hooks.on_step:
            await self.deps.hooks.on_step(node)

    def _flow(self, name: str, version: str) -> FlowPack:
        key = (name, version)
        if key not in self._flow_cache:
            self._flow_cache[key] = self.deps.flows.get(name, version)
        return self._flow_cache[key]

    def _phase(self, state: AgentState) -> tuple[FlowPack, FlowPhase]:
        flow = self._flow(state["flow_name"], state["flow_version"])
        return flow, flow.phase(state["phase"])

    async def _topo(self) -> Topology:
        now = time.monotonic()
        if self._topology is None or now - self._topology_at > 60:
            self._topology = await self.deps.telemetry.topology()
            self._topology_at = now
        return self._topology

    def _tool_context(
        self, incident: Incident, flow: FlowPack, phase: FlowPhase, run_id: str, topology: Topology
    ) -> ToolContext:
        return ToolContext(
            incident=incident,
            flow=flow,
            phase=phase,
            actor=Actor.agent(run_id),
            environment=self.deps.environment,
            telemetry=self.deps.telemetry,
            infrastructure=self.deps.infrastructure,
            memory_search=self.deps.memory_search,
            clock=self.clock,
            agent_run_id=uuid.UUID(run_id),
            known_components=frozenset(n.name for n in topology.nodes),
        )

    @staticmethod
    def _facts(
        state: AgentState, evidence: Sequence[Evidence], hypotheses: Sequence[Hypothesis]
    ) -> PhaseFacts:
        top = hypotheses[0] if hypotheses else None
        validated = any(
            h.status is HypothesisStatus.CONFIRMED
            or (h.status is HypothesisStatus.SUPPORTED and h.confirmed_tests() >= 1)
            for h in hypotheses
        )
        return PhaseFacts(
            iterations=state["iteration"],
            evidence_count=len(evidence),
            hypotheses_count=len(hypotheses),
            top_confidence=top.confidence if top else 0.0,
            hypothesis_validated=validated,
            action_planned=state.get("action_plan_id") is not None,
            no_action_required=state.get("decision") == "no_action",
        )

    async def _memory_text(self, incident: Incident) -> str:
        """Top similar past incidents, fetched once per incident and injected into every prompt."""
        key = str(incident.id)
        if key in self._memory_hints or self.deps.memory_search is None:
            return self._memory_hints.get(key, "")
        try:
            from aegis.domain.memory import SimilarIncidentQuery  # noqa: PLC0415

            matches = await self.deps.memory_search(
                SimilarIncidentQuery(
                    text=f"{incident.title}. {incident.summary}",
                    affected_services=list(incident.affected_services),
                    symptoms=[s.description for s in incident.signals[:6]],
                    limit=2,
                    exclude_incident_id=incident.id,
                )
            )
        except AegisError:
            matches = []
        lines = [
            f"- INC-{m.memory.incident_number} ({m.similarity:.0%} similar, {m.memory.outcome}): "
            f"root cause {m.memory.root_cause_service or 'unknown'} — {m.memory.root_cause[:140]}; "
            f"resolution: {m.memory.resolution[:120]}"
            for m in matches
            if m.similarity >= 0.35
        ]
        self._memory_hints[key] = "\n".join(lines)
        return self._memory_hints[key]

    async def _signal_status(self, incident: Incident) -> tuple[bool, list[str]]:
        """Deterministic "are the symptoms gone?" check behind the no-action guard.

        Reuses the verification engine so that the agent's notion of recovery is exactly the one
        the workflow will apply afterwards: every detection signal within its condition, nothing
        still accumulating, and no component that restarted in the last two minutes (a crash loop
        looks healthy for a minute after every restart).
        """
        baselines: dict[tuple[str, str], float | None] = {}
        for signal in incident.signals:
            baselines[(signal.service, signal.metric)] = await self.deps.telemetry.baseline(
                signal.service, signal.metric
            )
        spec = build_verification_spec(
            incident, baselines=baselines, target_service=None, tool_spec=None
        )
        lines: list[str] = []
        ok_all = True
        now = self.clock.now()
        topology = await self._topo()
        kinds = {n.name: n.kind for n in topology.nodes}
        for service in incident.affected_services:
            if kinds.get(service) not in ("service", "gateway"):
                continue
            try:
                ups = await self.deps.telemetry.metrics(
                    service, "up", start=now - timedelta(seconds=120), end=now
                )
            except AegisError:
                continue
            if ups.samples and min(ups.values) < 1.0:
                ok_all = False
                lines.append(f"{service} restarted within the last 120s → UNSTABLE")
        engine = VerificationEngine(
            self.deps.telemetry, clock=self.clock, sleep=_no_sleep, window_seconds=45
        )
        ok, results, _current, available = await engine.evaluate_once(spec, baselines)
        for result in results:
            lines.append(f"{result['detail']} → {'ok' if result['ok'] else 'ANOMALOUS'}")
        return (ok_all and ok and available), lines

    BENIGN_TAGS = frozenset({"normal", "ok", "not_reproduced", "stable"})

    @staticmethod
    def _diagnostic_backing(produced: list[Evidence], target: Hypothesis) -> list[Evidence]:
        """Evidence that a real tool execution produced, that implicates the suspected service and
        that does not itself say the service is healthy."""
        root = target.suspected_root_cause_service
        return [
            e
            for e in produced
            if e.tool_execution_id is not None
            and e.service == root
            and e.strength >= 0.75
            and not (set(e.tags) & AgentRuntime.BENIGN_TAGS)
        ]

    async def _judge(
        self,
        uow: UnitOfWork,
        state: AgentState,
        incident: Incident,
        call: ToolCallProposal,
        produced: list[Evidence],
        usage: BudgetUsage,
        topology: Topology,
    ) -> BudgetUsage:
        hypotheses = self.engine.rank(await uow.hypotheses.list_for_incident(incident.id))
        _text, handles = hypotheses_digest(hypotheses)
        target = handles.get(call.tests_hypothesis or "")
        if target is None:
            return usage
        lines = [e.summary for e in produced] or ["(no evidence produced)"]
        outcome: Literal["confirmed", "refuted", "inconclusive"] = "inconclusive"
        detail = ""
        if self.deps.llm is not None and state["llm_available"]:
            try:
                result = await self.deps.llm.complete_structured(
                    schema=TestJudgement,
                    system=JUDGE_SYSTEM,
                    user=build_judge_prompt(target, call.expectation, lines),
                    tier="reasoner",
                    cache_key=str(incident.id),
                )
                outcome, detail = result.value.outcome, result.value.detail
                usage = usage.add(llm_calls=1, llm_tokens=result.usage.total_tokens)
            except LLMError as exc:
                detail = f"judge unavailable: {exc}"
        strong = self._diagnostic_backing(produced, target)
        root = target.suspected_root_cause_service
        if not detail or detail.startswith("judge unavailable"):
            outcome = "confirmed" if strong else "inconclusive"
            detail = detail or (
                "deterministic judgement: diagnostic strongly implicates the suspected service"
                if strong
                else "deterministic judgement: not implicated"
            )
        elif outcome == "inconclusive" and strong:
            # the runtime overrules a timid judge when the diagnostic implicates the root
            outcome = "confirmed"
            detail = f"runtime: {strong[0].summary[:120]} strongly implicates {root}; {detail}"
        test = self.engine.judge_test(
            target,
            produced,
            outcome,
            tool_name=call.tool_name,
            expectation=call.expectation,
            tool_execution_id=None,
            now=self.clock.now(),
            detail=detail,
        )
        evidence_map = {e.id: e for e in await uow.evidence.list_for_incident(incident.id)}
        self.engine.rescore(
            target,
            evidence=evidence_map,
            incident=incident,
            topology=topology,
            now=self.clock.now(),
        )
        await uow.hypotheses.save(target)
        event_type = {
            "confirmed": EventType.HYPOTHESIS_VALIDATED,
            "refuted": EventType.HYPOTHESIS_REFUTED,
            "inconclusive": EventType.HYPOTHESIS_UPDATED,
        }[test.outcome]
        await emit(
            uow.incidents,
            self.deps.publisher,
            incident_id=incident.id,
            type=event_type,
            actor=Actor.agent(state["agent_run_id"]),
            title=f"Test {test.outcome}: {target.statement[:120]} "
            f"(confidence {target.confidence:.0%})",
            payload={**self._hypothesis_payload(target), "test": test.model_dump(mode="json")},
        )
        return usage

    async def _apply_update(
        self,
        uow: UnitOfWork,
        state: AgentState,
        incident: Incident,
        update: HypothesisUpdateProposal,
        hyp_handles: dict[str, Hypothesis],
        ev_handles: dict[str, Evidence],
        feedback: list[str],
    ) -> bool:
        target = hyp_handles.get(update.hypothesis)
        if target is None:
            feedback.append(f"unknown hypothesis handle {update.hypothesis}")
            return False
        for h in update.add_supporting_evidence:
            if h in ev_handles and ev_handles[h].id not in target.supporting_evidence_ids:
                target.supporting_evidence_ids.append(ev_handles[h].id)
        for h in update.add_contradicting_evidence:
            if h in ev_handles and ev_handles[h].id not in target.contradicting_evidence_ids:
                target.contradicting_evidence_ids.append(ev_handles[h].id)
        if update.abandon:
            target.status = HypothesisStatus.ABANDONED
        if update.test_outcome is not None:
            produced = [ev_handles[h] for h in update.add_supporting_evidence if h in ev_handles]
            outcome = update.test_outcome
            if outcome == "confirmed" and not self._diagnostic_backing(produced, target):
                # A hypothesis is confirmed by a diagnostic, never by the model asserting it.
                # `update_hypothesis` carries no tool execution of its own, so a `confirmed`
                # outcome here has to point at evidence a real tool produced.
                outcome = "inconclusive"
                feedback.append(
                    f"{update.hypothesis}: a test cannot be confirmed from an update; run a "
                    "diagnostic tool that implicates the suspected service"
                )
            self.engine.judge_test(
                target,
                produced,
                outcome,
                tool_name="model_update",
                expectation="",
                tool_execution_id=None,
                now=self.clock.now(),
                detail=update.test_detail,
            )
        await uow.hypotheses.save(target)
        return True

    def _rollback_plan(
        self, rem: RemediationProposal, flow: FlowPack, known: frozenset[str]
    ) -> RollbackPlan:
        if (
            rem.rollback_tool_name
            and rem.rollback_tool_name in flow.remediation_tools
            and self.deps.registry.has(rem.rollback_tool_name)
        ):
            try:
                args = parse_arguments(rem.rollback_arguments_json)
                self.deps.registry.get(rem.rollback_tool_name).parse_args(
                    args, known_components=known
                )
                return RollbackPlan(
                    available=True,
                    tool_name=rem.rollback_tool_name,
                    arguments=args,
                    reason="proposed by the agent and validated by the runtime",
                )
            except (ValueError, InvalidToolArguments):
                pass
        return default_rollback(rem.tool_name, parse_arguments(rem.arguments_json))

    @staticmethod
    def _hypothesis_payload(h: Hypothesis) -> dict[str, Any]:
        return {
            "hypothesis_id": str(h.id),
            "statement": h.statement,
            "category": h.category.value,
            "root_cause_service": h.suspected_root_cause_service,
            "status": h.status.value,
            "confidence": h.confidence,
            "score": h.score.model_dump(mode="json"),
            "supporting": len(h.supporting_evidence_ids),
            "contradicting": len(h.contradicting_evidence_ids),
        }

    async def _record_step(
        self,
        uow: UnitOfWork,
        state: AgentState,
        kind: AgentStepKind,
        node: str,
        title: str,
        input_data: dict[str, Any],
        output: dict[str, Any],
        *,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
        tool_execution_id: uuid.UUID | None = None,
    ) -> AgentStep:
        run_id = AgentRunId(uuid.UUID(state["agent_run_id"]))
        steps = await uow.agent_runs.steps_for_run(run_id)
        step = AgentStep(
            agent_run_id=run_id,
            incident_id=IncidentId(uuid.UUID(state["incident_id"])),
            seq=len(steps) + 1,
            phase=state["phase"],
            node=node,
            kind=kind,
            title=title[:200],
            input=input_data,
            output=output,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            tool_execution_id=tool_execution_id,
            at=self.clock.now(),
        )
        await uow.agent_runs.add_step(step)
        return step

    async def _load_or_create_run(
        self,
        uow: UnitOfWork,
        incident: Incident,
        flow: FlowPack,
        phase: str,
        run_id: uuid.UUID,
        usage: BudgetUsage,
        budget: ExecutionBudget,
        workflow_id: str | None,
        attempt: int,
    ) -> AgentRun:
        try:
            run = await uow.agent_runs.get(run_id)
            run.attempt = attempt
            run.status = AgentRunStatus.RUNNING
            return await uow.agent_runs.save(run)
        except AegisError:
            run = AgentRun(
                id=AgentRunId(run_id),
                incident_id=incident.id,
                flow_name=flow.name,
                flow_version=flow.version,
                phase=phase,
                budget=budget,
                usage=usage,
                model=self.deps.llm.model_for("reasoner") if self.deps.llm else "deterministic",
                started_at=self.clock.now(),
                workflow_id=workflow_id,
                attempt=attempt,
            )
            return await uow.agent_runs.add(run)

    async def _finish(self, run: AgentRun, final: AgentState, flow: FlowPack) -> PhaseOutcome:
        decision = final.get("decision") or "terminate"
        termination_value = final.get("termination")
        termination = (
            TerminationReason(termination_value)
            if termination_value
            else {
                "transition": TerminationReason.PHASE_COMPLETE,
                "action_planned": TerminationReason.ACTION_PLANNED,
                "no_action": TerminationReason.NO_ACTION_REQUIRED,
                "escalate": TerminationReason.ESCALATE,
            }.get(decision, TerminationReason.ERROR)
        )
        usage = BudgetUsage(**final["usage"])
        async with self.deps.uow_factory() as uow:
            run = await uow.agent_runs.get(run.id)
            run.status = (
                AgentRunStatus.BUDGET_EXHAUSTED
                if termination is TerminationReason.BUDGET_EXHAUSTED
                else AgentRunStatus.FAILED
                if termination is TerminationReason.ERROR
                else AgentRunStatus.COMPLETED
            )
            run.usage = usage
            run.finished_at = self.clock.now()
            run.termination_reason = termination
            run.summary = final.get("summary", "")
            run.steps_count = len(await uow.agent_runs.steps_for_run(run.id))
            run.model = final.get("model") or run.model
            await uow.agent_runs.save(run)
            AGENT_RUNS_TOTAL.labels(run.phase, termination.value).inc()
            AGENT_ITERATIONS.labels(run.phase).observe(final.get("iteration", 0))
            await emit(
                uow.incidents,
                self.deps.publisher,
                incident_id=run.incident_id,
                type=EventType.AGENT_RUN_FINISHED,
                actor=Actor.agent(str(run.id)),
                title=f"Agent finished phase '{run.phase}': {termination.value}",
                payload={
                    "agent_run_id": str(run.id),
                    "phase": run.phase,
                    "decision": decision,
                    "termination": termination.value,
                    "usage": usage.model_dump(),
                    "next_phase": final.get("next_phase"),
                    "steps": run.steps_count,
                    "summary": run.summary,
                },
            )
            await uow.commit()
        return PhaseOutcome(
            agent_run_id=run.id,
            incident_id=run.incident_id,
            phase=run.phase,
            decision=decision,
            trigger=final.get("trigger"),
            next_phase=final.get("next_phase"),
            termination=termination,
            action_plan_id=uuid.UUID(final["action_plan_id"])
            if final.get("action_plan_id")
            else None,
            iterations=final.get("iteration", 0),
            usage=usage,
            llm_used=final.get("llm_available", False)
            and final.get("model") != "deterministic-planner",
            steps=run.steps_count,
            summary=run.summary,
        )


__all__ = [
    "AgentDependencies",
    "AgentHooks",
    "AgentRuntime",
    "HypothesisProposal",
    "PhaseOutcome",
    "ToolExecutionRecord",
]
