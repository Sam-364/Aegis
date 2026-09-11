"""Policy rules, evaluation context and decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Actor, ValueObject
from aegis.domain.clock import utcnow
from aegis.domain.enums import (
    Environment,
    PolicyEffect,
    RiskLevel,
    Role,
    Severity,
    ToolCategory,
)
from aegis.domain.ids import PolicyDecisionId, new_id


class PolicyContext(ValueObject):
    """Everything a policy may match against. Built by the runtime, never by the LLM."""

    tool_name: str
    tool_category: ToolCategory
    tool_risk: RiskLevel
    environment: Environment
    severity: Severity
    flow_name: str | None
    phase: str | None
    actor: Actor
    incident_id: uuid.UUID | None = None
    in_agent_loop: bool = True
    arguments: dict[str, Any] = Field(default_factory=dict)
    remediation_attempt: int = 0
    tenant_id: str = "default"


class PolicyRule(ValueObject):
    name: str
    description: str = ""
    priority: int = 0  # higher evaluated first
    effect: PolicyEffect
    tools: frozenset[str] = Field(default_factory=frozenset)
    categories: frozenset[ToolCategory] = Field(default_factory=frozenset)
    min_risk: RiskLevel | None = None
    max_risk: RiskLevel | None = None
    environments: frozenset[Environment] = Field(default_factory=frozenset)
    severities: frozenset[Severity] = Field(default_factory=frozenset)
    phases: frozenset[str] = Field(default_factory=frozenset)
    flows: frozenset[str] = Field(default_factory=frozenset)
    actor_kinds: frozenset[str] = Field(default_factory=frozenset)
    required_role: Role | None = None
    in_agent_loop: bool | None = None
    reason: str = ""

    def matches(self, ctx: PolicyContext) -> bool:  # noqa: PLR0911 - explicit predicate chain
        if self.tools and ctx.tool_name not in self.tools:
            return False
        if self.categories and ctx.tool_category not in self.categories:
            return False
        if self.min_risk is not None and ctx.tool_risk.rank < self.min_risk.rank:
            return False
        if self.max_risk is not None and ctx.tool_risk.rank > self.max_risk.rank:
            return False
        if self.environments and ctx.environment not in self.environments:
            return False
        if self.severities and ctx.severity not in self.severities:
            return False
        if self.phases and (ctx.phase is None or ctx.phase not in self.phases):
            return False
        if self.flows and (ctx.flow_name is None or ctx.flow_name not in self.flows):
            return False
        if self.actor_kinds and ctx.actor.kind.value not in self.actor_kinds:
            return False
        if self.in_agent_loop is not None and ctx.in_agent_loop != self.in_agent_loop:
            return False
        return not (self.required_role is not None and not ctx.actor.has_role(self.required_role))


class PolicyDecision(ValueObject):
    id: PolicyDecisionId = Field(default_factory=lambda: PolicyDecisionId(new_id()))
    effect: PolicyEffect
    matched_rule: str | None
    reason: str
    evaluated_rules: tuple[str, ...] = ()
    invariant: str | None = None  # set when a hard-coded invariant decided
    context: PolicyContext
    decided_at: datetime = Field(default_factory=utcnow)

    @property
    def allowed(self) -> bool:
        return self.effect is PolicyEffect.ALLOW
