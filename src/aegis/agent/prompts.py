"""Prompt construction. The system prompt is stable (cache-friendly); the user prompt carries the
current incident state as compact structured text. No hidden reasoning is requested or shown."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from aegis.domain.flow import BudgetUsage, ExecutionBudget, FlowPack, FlowPhase
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident
from aegis.domain.telemetry import Topology

SYSTEM_PROMPT = """You are the reasoning component of Aegis, an autonomous incident-response runtime.

You do not operate infrastructure. You PROPOSE one step at a time; the runtime authorizes, executes,
records and verifies. Anything you propose outside the allowed tools or the current phase is refused
and the refusal is shown back to you, so read the allowed tool list carefully.

How to investigate well:
- Symptoms at the edge (api-gateway latency or errors) are almost always inherited from a dependency.
  Walk the dependency graph downstream until you find the component that is failing on its own.
- Shared resources (postgres, redis) saturate because of a client. The component that HOLDS the
  connections (top client, leaked/idle-in-transaction by service) is the root cause, not the resource.
- A recent deployment that precedes the incident on a service whose own error rate rose is a
  regression until proven otherwise; restarts do not fix regressions, rollbacks do.
- A dependency that is down explains every caller above it. Fix the dependency, never the callers.
- If metrics have already returned to baseline on their own, the correct action is to conclude that
  no remediation is required.
- Each hypothesis names ONE mechanism on ONE service. If the evidence suggests two mechanisms,
  propose two hypotheses; never write "X is broken and also leaking Y" as one statement.
- A diagnostic that refutes a secondary detail does not refute the root cause. When a test refutes
  part of a hypothesis, propose the corrected single-mechanism hypothesis instead of giving up.
- Prefer the single tool call that best discriminates between the live hypotheses. Do not repeat a
  call whose result you already have unless time has passed and the value could have changed.

Output contract:
- Return exactly one action per step in the required JSON schema.
- Reference evidence by its handle (E1, E2 …) and hypotheses by their handle (H1, H2 …).
- `arguments_json` must be a JSON object using only the documented argument names.
- Keep `observation` and `rationale` short, factual and free of speculation about hidden state.
- Never invent metrics, services, versions or evidence that is not in the digest.
"""

PHASE_ACTIONS: dict[str, tuple[str, ...]] = {
    "triage": ("call_tool", "phase_complete", "conclude_no_action", "escalate"),
    "investigate": (
        "call_tool",
        "propose_hypotheses",
        "phase_complete",
        "conclude_no_action",
        "escalate",
    ),
    "hypothesize": (
        "propose_hypotheses",
        "update_hypothesis",
        "call_tool",
        "phase_complete",
        "conclude_no_action",
        "escalate",
    ),
    "validate": (
        "call_tool",
        "update_hypothesis",
        "propose_hypotheses",
        "phase_complete",
        "conclude_no_action",
        "escalate",
    ),
    "remediate": ("plan_remediation", "call_tool", "conclude_no_action", "escalate"),
}


def allowed_actions(phase: FlowPhase) -> tuple[str, ...]:
    return PHASE_ACTIONS.get(phase.name, ("call_tool", "phase_complete", "escalate"))


def topology_digest(topology: Topology) -> str:
    lines = []
    for node in sorted(topology.nodes, key=lambda n: (n.tier, n.name)):
        deps = topology.dependencies_of(node.name)
        lines.append(
            f"- {node.name} ({node.kind}, tier {node.tier}, {node.replicas} replicas, "
            f"v{node.version})" + (f" -> {', '.join(deps)}" if deps else "")
        )
    return "\n".join(lines)


def hypotheses_digest(hypotheses: Sequence[Hypothesis]) -> tuple[str, dict[str, Hypothesis]]:
    handles: dict[str, Hypothesis] = {}
    lines: list[str] = []
    for i, h in enumerate(hypotheses):
        handle = f"H{i + 1}"
        handles[handle] = h
        tests = ""
        if h.tests:
            tests = " tests=" + ",".join(t.outcome for t in h.tests)
        lines.append(
            f"[{handle} {h.status.value} conf={h.confidence:.2f} root={h.suspected_root_cause_service} "
            f"cat={h.category.value}{tests}] {h.statement} | score: "
            f"{'; '.join(h.score.explanation[:3])}"
        )
    return ("\n".join(lines) if lines else "(none yet)"), handles


def tools_digest(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return "(no tools allowed in this phase)"
    return "\n".join(f"- {t['name']}({t['arguments']}): {t['description']}" for t in tools)


def build_user_prompt(
    *,
    incident: Incident,
    flow: FlowPack,
    phase: FlowPhase,
    topology: Topology,
    evidence_text: str,
    hypotheses_text: str,
    tools: list[dict[str, Any]],
    feedback: Sequence[str],
    budget: ExecutionBudget,
    usage: BudgetUsage,
    iteration: int,
    now: datetime,
    remediation_tools: Sequence[str] = (),
    recent_metrics: str = "",
    memory_text: str = "",
    signals_recovered: bool = False,
) -> str:
    elapsed = int(incident.duration_seconds(now))
    signals = (
        "\n".join(
            f"- {s.service} {s.metric}: {s.baseline_value:.3g} -> {s.observed_value:.3g} "
            f"({s.kind.value}, {s.deviation_sigma:.1f} sigma) at {s.detected_at.strftime('%H:%M:%S')}"
            for s in incident.signals[:8]
        )
        or "- (no detection signals recorded)"
    )
    actions = ", ".join(allowed_actions(phase))
    parts = [
        f"# Incident {incident.display_id} — {incident.title}",
        f"severity {incident.severity.value}, status {incident.status.value}, open for {elapsed}s, "
        f"environment {incident.environment.value}",
        f"affected services: {', '.join(incident.affected_services)}",
        "detection signals:",
        signals,
        "",
        "# Topology",
        topology_digest(topology),
        "",
        f"# Flow {flow.ref} — phase '{phase.name}' (iteration {iteration + 1}/{phase.max_iterations})",
        f"objective: {phase.objective}",
    ]
    if phase.guidance:
        parts.append(f"guidance: {phase.guidance.strip()}")
    parts += [
        f"allowed actions in this phase: {actions}",
        f"exit conditions: {', '.join(f'{c.kind}' + (f'>={c.value:g}' if c.value is not None else '') for c in phase.exit_conditions) or 'none'}",
        "",
        "# Allowed tools (only these can be called now)",
        tools_digest(tools),
    ]
    if phase.plans_remediation:
        parts += [
            "",
            "# Remediation tools available to the workflow (plan_remediation only)",
            ", ".join(sorted(remediation_tools)) or "(none)",
            "The remediation must target the root-cause service of a CONFIRMED or SUPPORTED "
            "hypothesis with confidence >= 0.55. Provide reason, expected_effect and, if "
            "reversible, a rollback tool.",
        ]
    if recent_metrics:
        header = (
            "ALL DETECTION SIGNALS ARE BACK WITHIN BASELINE"
            if signals_recovered
            else "detection signals are still anomalous"
        )
        parts += ["", f"# Current vs baseline (runtime computed) — {header}", recent_metrics]
        if signals_recovered:
            parts.append(
                "The symptoms that opened this incident are no longer present and nothing has been "
                "remediated. Unless the evidence shows an unverified change of your own, conclude "
                "that no action is required."
            )
    if memory_text:
        parts += ["", "# Similar past incidents (memory — evidence, not truth)", memory_text]
    parts += ["", "# Evidence", evidence_text, "", "# Hypotheses", hypotheses_text]
    if feedback:
        parts += ["", "# Runtime feedback on your recent steps (read carefully)"]
        parts += [f"- {f}" for f in feedback[-6:]]
    remaining_calls = max(0, budget.max_tool_calls - usage.tool_calls)
    remaining_iters = max(0, budget.max_iterations - usage.iterations)
    parts += [
        "",
        f"# Budget: {remaining_calls} tool calls and {remaining_iters} iterations remain "
        f"across the whole incident; {phase.max_iterations - iteration} in this phase.",
        "",
        "Decide the single best next step now.",
    ]
    return "\n".join(parts)


JUDGE_SYSTEM = """You judge whether a diagnostic result confirms, refutes, or is inconclusive for a
hypothesis. Be strict: 'confirmed' only if the result directly implicates the suspected root-cause
service in the stated mechanism; 'refuted' only if the result contradicts it. Answer in the schema."""


def build_judge_prompt(
    hypothesis: Hypothesis, expectation: str, evidence_lines: Sequence[str]
) -> str:
    return "\n".join(
        [
            f"Hypothesis: {hypothesis.statement}",
            f"Suspected root cause service: {hypothesis.suspected_root_cause_service}",
            f"Mechanism: {hypothesis.mechanism or 'n/a'}",
            f"Expectation stated before the test: {expectation or 'n/a'}",
            "Diagnostic result:",
            *[f"- {line}" for line in evidence_lines],
        ]
    )


MEMORY_SYSTEM = """Summarize a resolved incident for an incident-memory database. Be concrete: name
services, metrics, versions and actions. Symptoms are observable facts; lessons are short and
actionable."""
