"""Flow runtime: resolves phases, evaluates declarative exit conditions, decides transitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.domain.flow import ExitCondition, FlowPack, FlowPhase, TransitionTrigger


@dataclass(frozen=True)
class PhaseFacts:
    """Runtime facts the exit conditions are evaluated against. Computed by the runtime, never
    supplied by the LLM."""

    iterations: int = 0
    evidence_count: int = 0
    hypotheses_count: int = 0
    top_confidence: float = 0.0
    hypothesis_validated: bool = False
    action_planned: bool = False
    no_action_required: bool = False


@dataclass
class ExitEvaluation:
    met: bool
    satisfied: list[str] = field(default_factory=list)
    unsatisfied: list[str] = field(default_factory=list)


class FlowRuntime:
    def __init__(self, pack: FlowPack) -> None:
        self.pack = pack

    def phase(self, name: str) -> FlowPhase:
        return self.pack.phase(name)

    def initial_phase(self) -> FlowPhase:
        return self.pack.phase(self.pack.initial_phase)

    def evaluate_exit(self, phase: FlowPhase, facts: PhaseFacts) -> ExitEvaluation:
        satisfied: list[str] = []
        unsatisfied: list[str] = []
        for cond in phase.exit_conditions:
            (satisfied if self._check(cond, facts) else unsatisfied).append(self._label(cond))
        met = not unsatisfied if phase.exit_conditions else facts.iterations > 0
        return ExitEvaluation(met=met, satisfied=satisfied, unsatisfied=unsatisfied)

    def exhausted(self, phase: FlowPhase, facts: PhaseFacts) -> bool:
        return facts.iterations >= phase.max_iterations

    def decide(self, phase: FlowPhase, facts: PhaseFacts) -> tuple[TransitionTrigger, str | None]:
        """Return (trigger, next phase name or None when staying)."""
        if facts.no_action_required and phase.next_phase("no_action_required") is not None:
            return "no_action_required", phase.next_phase("no_action_required")
        evaluation = self.evaluate_exit(phase, facts)
        if evaluation.met:
            nxt = phase.next_phase("exit_conditions_met")
            if nxt is not None:
                return "exit_conditions_met", nxt
        if self.exhausted(phase, facts):
            nxt = phase.next_phase("exhausted") or phase.next_phase("escalate")
            return "exhausted", nxt
        return "exit_conditions_met", None

    @staticmethod
    def _check(cond: ExitCondition, facts: PhaseFacts) -> bool:  # noqa: PLR0911 - table
        v = cond.value or 0
        match cond.kind:
            case "min_evidence":
                return facts.evidence_count >= v
            case "min_hypotheses":
                return facts.hypotheses_count >= v
            case "min_hypothesis_confidence":
                return facts.top_confidence >= v
            case "hypothesis_validated":
                return facts.hypothesis_validated
            case "action_planned":
                return facts.action_planned
            case "no_action_required":
                return facts.no_action_required
            case "min_iterations":
                return facts.iterations >= v

    @staticmethod
    def _label(cond: ExitCondition) -> str:
        return f"{cond.kind}" + (f">={cond.value:g}" if cond.value is not None else "")
