"""Flow packs: declarative, versioned investigation programmes.

A ``FlowPack`` is compiled from YAML and validated at startup. Phases enumerate the tools the
agent may *request*; the authorizer, not the phase, decides whether a request executes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from aegis.domain.base import ValueObject
from aegis.domain.enums import RiskLevel, Severity, SignalKind

ExitConditionKind = Literal[
    "min_evidence",
    "min_hypotheses",
    "min_hypothesis_confidence",
    "hypothesis_validated",
    "action_planned",
    "no_action_required",
    "min_iterations",
]

TransitionTrigger = Literal["exit_conditions_met", "exhausted", "escalate", "no_action_required"]


class ExecutionBudget(ValueObject):
    max_iterations: int = Field(default=20, ge=1, le=200)
    max_tool_calls: int = Field(default=40, ge=1, le=500)
    max_llm_calls: int = Field(default=30, ge=0, le=500)
    max_llm_tokens: int = Field(default=200_000, ge=0)
    max_runtime_seconds: int = Field(default=900, ge=10)
    max_remediation_attempts: int = Field(default=2, ge=0, le=5)

    def scaled(self, factor: float) -> ExecutionBudget:
        return ExecutionBudget(
            max_iterations=max(1, round(self.max_iterations * factor)),
            max_tool_calls=max(1, round(self.max_tool_calls * factor)),
            max_llm_calls=max(0, round(self.max_llm_calls * factor)),
            max_llm_tokens=max(0, round(self.max_llm_tokens * factor)),
            max_runtime_seconds=max(10, round(self.max_runtime_seconds * factor)),
            max_remediation_attempts=self.max_remediation_attempts,
        )


class BudgetUsage(ValueObject):
    iterations: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0
    runtime_seconds: float = 0.0
    remediation_attempts: int = 0

    def exceeded(self, budget: ExecutionBudget) -> list[str]:
        reasons: list[str] = []
        if self.iterations >= budget.max_iterations:
            reasons.append(f"iterations {self.iterations} >= {budget.max_iterations}")
        if self.tool_calls >= budget.max_tool_calls:
            reasons.append(f"tool_calls {self.tool_calls} >= {budget.max_tool_calls}")
        if self.llm_calls >= budget.max_llm_calls:
            reasons.append(f"llm_calls {self.llm_calls} >= {budget.max_llm_calls}")
        if self.llm_tokens >= budget.max_llm_tokens:
            reasons.append(f"llm_tokens {self.llm_tokens} >= {budget.max_llm_tokens}")
        if self.runtime_seconds >= budget.max_runtime_seconds:
            reasons.append(f"runtime {self.runtime_seconds:.0f}s >= {budget.max_runtime_seconds}s")
        return reasons

    def add(self, **deltas: float) -> BudgetUsage:
        data = self.model_dump()
        for key, delta in deltas.items():
            data[key] = data[key] + delta
        return BudgetUsage(**data)


class ExitCondition(ValueObject):
    kind: ExitConditionKind
    value: float | None = None
    description: str = ""


class PhaseTransition(ValueObject):
    on: TransitionTrigger
    to: str


class FlowPhase(ValueObject):
    name: str
    objective: str
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    max_iterations: int = Field(default=6, ge=0, le=100)
    timeout_seconds: int = Field(default=300, ge=5)
    risk_ceiling: RiskLevel = RiskLevel.NONE
    exit_conditions: tuple[ExitCondition, ...] = ()
    transitions: tuple[PhaseTransition, ...] = ()
    terminal: bool = False
    guidance: str = ""  # extra instructions handed to the LLM for this phase
    plans_remediation: bool = False

    def next_phase(self, trigger: TransitionTrigger) -> str | None:
        return next((t.to for t in self.transitions if t.on == trigger), None)


class FlowPack(ValueObject):
    name: str
    version: str
    description: str = ""
    applies_to: frozenset[SignalKind] = Field(default_factory=frozenset)
    priority: int = 0  # higher wins when several packs apply
    initial_phase: str
    phases: tuple[FlowPhase, ...]
    remediation_tools: frozenset[str] = Field(default_factory=frozenset)
    remediation_risk_ceiling: RiskLevel = RiskLevel.HIGH
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    severity_budget_factor: dict[Severity, float] = Field(
        default_factory=lambda: {
            Severity.SEV1: 1.5,
            Severity.SEV2: 1.25,
            Severity.SEV3: 1.0,
            Severity.SEV4: 0.75,
        }
    )
    checksum: str = ""

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def all_tools(self) -> frozenset[str]:
        """Every tool the pack references: phase tools plus workflow-side remediation tools."""
        tools: set[str] = set(self.remediation_tools)
        for phase in self.phases:
            tools |= phase.allowed_tools
        return frozenset(tools)

    @property
    def loop_tools(self) -> frozenset[str]:
        """Tools the agent may request inside the reasoning loop."""
        tools: set[str] = set()
        for phase in self.phases:
            tools |= phase.allowed_tools
        return frozenset(tools)

    def phase(self, name: str) -> FlowPhase:
        for phase in self.phases:
            if phase.name == name:
                return phase
        raise KeyError(name)

    def has_phase(self, name: str) -> bool:
        return any(p.name == name for p in self.phases)

    def budget_for(self, severity: Severity) -> ExecutionBudget:
        return self.budget.scaled(self.severity_budget_factor.get(severity, 1.0))

    @model_validator(mode="after")
    def _validate_graph(self) -> FlowPack:
        names = [p.name for p in self.phases]
        if len(names) != len(set(names)):
            raise ValueError("phase names must be unique")
        if self.initial_phase not in names:
            raise ValueError(f"initial phase '{self.initial_phase}' is not defined")
        for phase in self.phases:
            for t in phase.transitions:
                if t.to not in names:
                    raise ValueError(f"phase '{phase.name}' transitions to unknown '{t.to}'")
            if not phase.terminal and not phase.transitions:
                raise ValueError(f"non-terminal phase '{phase.name}' has no transitions")
            if phase.terminal and phase.allowed_tools:
                raise ValueError(f"terminal phase '{phase.name}' must not allow tools")
        if not any(p.terminal for p in self.phases):
            raise ValueError("flow must have at least one terminal phase")
        return self
