"""Deterministic planner: phase playbooks that work without any LLM.

It is the fallback when the model is unavailable, and the baseline the evals compare the model
against. It is intentionally simple and transparent.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from aegis.agent.schemas import AgentProposal, RemediationProposal, ToolCallProposal
from aegis.domain.enums import HypothesisCategory, HypothesisStatus
from aegis.domain.evidence import Evidence
from aegis.domain.flow import FlowPhase
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident
from aegis.domain.telemetry import Topology
from aegis.hypotheses.engine import attributions, deterministic_hypotheses
from aegis.remediation.planning import category_default_action


@dataclass
class PlannerMemory:
    executed: set[tuple[str, str]] = field(default_factory=set)

    def seen(self, tool: str, args: dict[str, Any]) -> bool:
        return (tool, json.dumps(args, sort_keys=True)) in self.executed

    def remember(self, tool: str, args: dict[str, Any]) -> None:
        self.executed.add((tool, json.dumps(args, sort_keys=True)))


class DeterministicPlanner:
    def __init__(self, memory: PlannerMemory | None = None) -> None:
        self.memory = memory or PlannerMemory()

    def propose(  # noqa: PLR0911 - one return per phase playbook
        self,
        *,
        incident: Incident,
        phase: FlowPhase,
        topology: Topology,
        evidence: Sequence[Evidence],
        hypotheses: Sequence[Hypothesis],
        hypothesis_handles: dict[str, Hypothesis],
        remediation_tools: frozenset[str],
        metrics_recovered: bool,
    ) -> AgentProposal:
        if metrics_recovered and phase.name in (
            "investigate",
            "hypothesize",
            "validate",
            "remediate",
        ):
            return AgentProposal(
                observation="Incident metrics are back within baseline.",
                action="conclude_no_action",
                rationale="deterministic planner: symptoms cleared without action",
            )
        match phase.name:
            case "triage":
                return self._next_call(self._triage_queue(incident, topology), phase) or self._done(
                    "triage queue exhausted"
                )
            case "investigate":
                call = self._next_call(self._investigate_queue(incident, topology, evidence), phase)
                if call is not None:
                    return call
                return self._hypothesize(incident, evidence, topology, hypotheses)
            case "hypothesize":
                if not hypotheses:
                    return self._hypothesize(incident, evidence, topology, hypotheses)
                return self._done("hypotheses proposed deterministically")
            case "validate":
                return self._validate(hypotheses, hypothesis_handles, phase, topology)
            case "remediate":
                return self._remediate(
                    hypotheses, hypothesis_handles, topology, remediation_tools, evidence
                )
        return AgentProposal(
            observation="unknown phase",
            action="escalate",
            rationale="deterministic planner has no playbook for this phase",
        )

    # --- queues -----------------------------------------------------------------------------------

    def _triage_queue(
        self, incident: Incident, topology: Topology
    ) -> list[tuple[str, dict[str, Any]]]:
        queue: list[tuple[str, dict[str, Any]]] = []
        symptomatic = list(dict.fromkeys(s.service for s in incident.signals)) or list(
            incident.affected_services
        )
        for svc in symptomatic:
            queue.append(("get_health", {"service": svc}))
        for signal in incident.signals[:4]:
            queue.append(("compare_baseline", {"service": signal.service, "metric": signal.metric}))
        kinds = {n.name: n.kind for n in topology.nodes}
        for svc in symptomatic:
            if kinds.get(svc) in ("gateway", "service"):
                queue.append(("inspect_dependencies", {"service": svc}))
        return queue

    def _investigate_queue(
        self, incident: Incident, topology: Topology, evidence: Sequence[Evidence]
    ) -> list[tuple[str, dict[str, Any]]]:
        queue: list[tuple[str, dict[str, Any]]] = []
        kinds = {n.name: n.kind for n in topology.nodes}
        symptomatic = list(dict.fromkeys(s.service for s in incident.signals)) or list(
            incident.affected_services
        )
        closure: list[str] = []
        for svc in symptomatic:
            for dep in [svc, *topology.downstream_closure(svc)]:
                if dep not in closure:
                    closure.append(dep)
        for comp in closure:
            if kinds.get(comp) == "cache":
                queue.append(("inspect_redis", {"component": comp}))
            elif kinds.get(comp) == "database":
                queue.append(("inspect_database", {"component": comp}))
        # dependencies of the highest-tier symptomatic services: which dependency is unhealthy?
        tiers = {n.name: n.tier for n in topology.nodes}
        for svc in sorted(symptomatic, key=lambda n: tiers.get(n, 9))[:2]:
            if kinds.get(svc) in ("gateway", "service"):
                queue.append(("inspect_dependencies", {"service": svc}))
        implicated = [
            e.service
            for e in sorted(evidence, key=lambda e: -e.strength)
            if e.service and e.strength >= 0.6 and kinds.get(e.service) in ("service", "gateway")
        ]
        queue.append(("query_traces", {"errors_only": True}))
        for svc in list(dict.fromkeys([*implicated, *closure]))[:4]:
            if kinds.get(svc) in ("service", "gateway"):
                queue.append(("inspect_deployment", {"service": svc}))
        for svc in list(dict.fromkeys([*implicated, *symptomatic]))[:3]:
            if kinds.get(svc) in ("service", "gateway"):
                queue.append(("get_logs", {"service": svc, "level": "WARN"}))
                queue.append(("inspect_service", {"service": svc}))
        queue.append(("search_incident_memory", {"query": incident.title}))
        return queue

    def _next_call(
        self, queue: list[tuple[str, dict[str, Any]]], phase: FlowPhase
    ) -> AgentProposal | None:
        for tool, args in queue:
            if tool in phase.allowed_tools and not self.memory.seen(tool, args):
                self.memory.remember(tool, args)
                return AgentProposal(
                    observation="Collecting evidence along the dependency chain.",
                    action="call_tool",
                    tool_call=ToolCallProposal(
                        tool_name=tool,
                        arguments_json=json.dumps(args),
                        purpose="deterministic playbook step",
                    ),
                    rationale="deterministic planner",
                )
        return None

    def _done(self, why: str) -> AgentProposal:
        return AgentProposal(
            observation=why, action="phase_complete", rationale="deterministic planner"
        )

    def _hypothesize(
        self,
        incident: Incident,
        evidence: Sequence[Evidence],
        topology: Topology,
        hypotheses: Sequence[Hypothesis],
    ) -> AgentProposal:
        proposals = deterministic_hypotheses(incident, list(evidence), topology)
        existing = {h.suspected_root_cause_service for h in hypotheses}
        proposals = [p for p in proposals if p.suspected_root_cause_service not in existing]
        if not proposals:
            return self._done("no further hypotheses derivable from evidence")
        return AgentProposal(
            observation="Evidence implicates specific components.",
            action="propose_hypotheses",
            hypotheses=proposals,
            rationale="deterministic planner derived hypotheses from evidence strength",
        )

    def _validate(  # noqa: PLR0912 - one branch per hypothesis category
        self,
        hypotheses: Sequence[Hypothesis],
        handles: dict[str, Hypothesis],
        phase: FlowPhase,
        topology: Topology,
    ) -> AgentProposal:
        live = [
            h
            for h in hypotheses
            if h.status not in (HypothesisStatus.REFUTED, HypothesisStatus.ABANDONED)
        ]
        if not live:
            return AgentProposal(
                observation="No live hypothesis to validate.",
                action="escalate",
                rationale="deterministic planner",
            )
        # Test the best candidate that has not been tested yet. Always re-testing the top-ranked
        # hypothesis is how `bad-deployment` used to cycle: a generic "order-service is the most
        # implicated component" ranked first, its diagnostic came back inconclusive, and the
        # runner-up — the payment-service deployment that actually caused it — was never reached
        # before the cycle guard escalated the incident.
        untested = [h for h in live if not h.tests]
        top = untested[0] if untested else live[0]
        handle = next((k for k, v in handles.items() if v.id == top.id), "H1")
        kinds = {n.name: n.kind for n in topology.nodes}
        root = top.suspected_root_cause_service or ""
        candidates: list[tuple[str, dict[str, Any]]] = []
        is_service = kinds.get(root) in ("service", "gateway")
        match top.category:
            case HypothesisCategory.RESOURCE_EXHAUSTION:
                for dep in [root, *topology.dependencies_of(root)]:
                    if kinds.get(dep) == "cache":
                        candidates.append(("run_cache_diagnostic", {"component": dep}))
                    if kinds.get(dep) == "database":
                        candidates.append(("run_database_diagnostic", {"component": dep}))
                if is_service:
                    candidates.append(("run_process_diagnostic", {"service": root}))
            case HypothesisCategory.DEPLOYMENT_REGRESSION:
                candidates.append(("compare_baseline", {"service": root, "metric": "error_rate"}))
                candidates.append(("reproduce_issue", {"service": root, "endpoint": "/checkout"}))
                candidates.append(("run_process_diagnostic", {"service": root}))
            case HypothesisCategory.DEPENDENCY_FAILURE:
                if is_service:
                    candidates.append(("run_process_diagnostic", {"service": root}))
                candidates.append(("compare_baseline", {"service": root, "metric": "up"}))
                for caller in topology.dependents_of(root)[:1]:
                    candidates.append(("run_connectivity_test", {"source": caller, "target": root}))
            case HypothesisCategory.CAPACITY:
                candidates.append(("compare_baseline", {"service": root, "metric": "cpu_percent"}))
                candidates.append(("run_process_diagnostic", {"service": root}))
                candidates.append(("run_load_projection", {"service": root, "factor": 1.5}))
            case _:
                if is_service:
                    candidates.append(("run_process_diagnostic", {"service": root}))
        for tool, args in candidates:
            if tool in phase.allowed_tools and not self.memory.seen(tool, args):
                self.memory.remember(tool, args)
                return AgentProposal(
                    observation=f"Testing {handle}: {top.statement}",
                    action="call_tool",
                    tool_call=ToolCallProposal(
                        tool_name=tool,
                        arguments_json=json.dumps(args),
                        purpose="validate leading hypothesis",
                        tests_hypothesis=handle,
                        expectation=f"result implicates {root}",
                    ),
                    rationale="deterministic planner",
                )
        return self._done("validation diagnostics exhausted")

    def _remediate(
        self,
        hypotheses: Sequence[Hypothesis],
        handles: dict[str, Hypothesis],
        topology: Topology,
        remediation_tools: frozenset[str],
        evidence: Sequence[Evidence],
    ) -> AgentProposal:
        confirmed = [
            h
            for h in hypotheses
            if h.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.SUPPORTED)
        ]
        if not confirmed:
            return AgentProposal(
                observation="No confirmed hypothesis.",
                action="escalate",
                rationale="deterministic planner will not remediate unconfirmed causes",
            )
        top = confirmed[0]
        handle = next((k for k, v in handles.items() if v.id == top.id), "H1")
        if top.category is HypothesisCategory.TRANSIENT:
            return AgentProposal(
                observation="Leading hypothesis is transient.",
                action="conclude_no_action",
                rationale="deterministic planner",
            )
        choice = category_default_action(top, topology, remediation_tools, evidence=evidence)
        if choice is None and top.category is HypothesisCategory.RESOURCE_EXHAUSTION:
            # the root is a shared resource; find its heaviest client from evidence tags/data
            client = self._heaviest_client(top, topology, evidence)
            if client and "rotate_connection_pool" in remediation_tools:
                choice = (
                    "rotate_connection_pool",
                    {"service": client, "target": top.suspected_root_cause_service},
                )
            elif client and "restart_service" in remediation_tools:
                choice = ("restart_service", {"service": client})
        if choice is None:
            return AgentProposal(
                observation="No safe deterministic remediation for this cause.",
                action="escalate",
                rationale="deterministic planner",
            )
        tool, args = choice
        return AgentProposal(
            observation=f"Planning remediation for {handle}.",
            action="plan_remediation",
            remediation=RemediationProposal(
                tool_name=tool,
                arguments_json=json.dumps(args),
                target_hypothesis=handle,
                reason=f"addresses {top.category.value} on {top.suspected_root_cause_service}",
                expected_effect="symptomatic metrics return to baseline",
                rollback_tool_name=None,
                rollback_arguments_json="{}",
            ),
            rationale="deterministic planner category playbook",
        )

    @staticmethod
    def _heaviest_client(
        hypothesis: Hypothesis, topology: Topology, evidence: Sequence[Evidence]
    ) -> str | None:
        root = hypothesis.suspected_root_cause_service or ""
        for a in sorted(attributions(evidence), key=lambda a: -a.share):
            if a.resource == root and a.client != root:
                return a.client
        clients = topology.dependents_of(root)
        return clients[0] if clients else None
