"""Policy evaluation: invariants first, then rules by priority, default DENY."""

from __future__ import annotations

from collections.abc import Iterable

from aegis.domain.enums import ActorKind, PolicyEffect, ToolCategory
from aegis.domain.policy import PolicyContext, PolicyDecision, PolicyRule


class PolicyEngine:
    """Evaluates a ``PolicyContext``.

    Invariants cannot be overridden by YAML:

    * DANGEROUS tools are always denied.
    * MUTATING tools are denied inside the agent loop; they execute only through the workflow.
    * The agent (as an actor) may never trigger a MUTATING tool directly.
    """

    def __init__(self, rules: Iterable[PolicyRule]) -> None:
        self.rules = sorted(rules, key=lambda r: -r.priority)

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        invariant = self._invariants(ctx)
        if invariant is not None:
            return invariant
        evaluated: list[str] = []
        for rule in self.rules:
            evaluated.append(rule.name)
            if rule.matches(ctx):
                return PolicyDecision(
                    effect=rule.effect,
                    matched_rule=rule.name,
                    reason=rule.reason or rule.description or f"matched rule {rule.name}",
                    evaluated_rules=tuple(evaluated),
                    context=ctx,
                )
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            matched_rule=None,
            reason="no policy rule matched; default is deny",
            evaluated_rules=tuple(evaluated),
            context=ctx,
        )

    @staticmethod
    def _invariants(ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_category is ToolCategory.DANGEROUS:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                matched_rule=None,
                invariant="dangerous_tools_denied",
                reason="dangerous tools are never executable",
                context=ctx,
            )
        if ctx.tool_category is ToolCategory.MUTATING and ctx.in_agent_loop:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                matched_rule=None,
                invariant="no_mutation_inside_agent_loop",
                reason="mutating tools execute only through the durable workflow after "
                "policy evaluation and approval",
                context=ctx,
            )
        if ctx.tool_category is ToolCategory.MUTATING and ctx.actor.kind is ActorKind.AGENT:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                matched_rule=None,
                invariant="agent_cannot_mutate",
                reason="the agent proposes; only the workflow or a human may execute mutations",
                context=ctx,
            )
        return None
