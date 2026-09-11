from __future__ import annotations

from pathlib import Path

import pytest

from aegis.domain.base import Actor
from aegis.domain.enums import Environment, PolicyEffect, RiskLevel, Role, Severity, ToolCategory
from aegis.domain.errors import PolicyDefinitionError
from aegis.domain.policy import PolicyContext, PolicyRule
from aegis.policy.engine import PolicyEngine
from aegis.policy.loader import load_policy_dir, parse_rule

ROOT = Path(__file__).resolve().parents[2]


def ctx(**over: object) -> PolicyContext:
    base: dict[str, object] = {
        "tool_name": "restart_service",
        "tool_category": ToolCategory.MUTATING,
        "tool_risk": RiskLevel.MEDIUM,
        "environment": Environment.DEVELOPMENT,
        "severity": Severity.SEV2,
        "flow_name": "incident-investigation",
        "phase": "remediate",
        "actor": Actor.workflow("wf"),
        "in_agent_loop": False,
    }
    base.update(over)
    return PolicyContext(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine(load_policy_dir(ROOT / "policies"))


def test_default_policy_matrix(engine: PolicyEngine) -> None:
    assert (
        engine.evaluate(
            ctx(
                tool_name="get_metrics",
                tool_category=ToolCategory.READ_ONLY,
                tool_risk=RiskLevel.NONE,
                in_agent_loop=True,
                actor=Actor.agent("r"),
            )
        ).effect
        is PolicyEffect.ALLOW
    )
    assert (
        engine.evaluate(
            ctx(
                tool_name="run_cache_diagnostic",
                tool_category=ToolCategory.DIAGNOSTIC,
                tool_risk=RiskLevel.LOW,
                in_agent_loop=True,
                actor=Actor.agent("r"),
            )
        ).effect
        is PolicyEffect.ALLOW
    )
    # medium-risk mutation in development requires approval
    d = engine.evaluate(ctx())
    assert (
        d.effect is PolicyEffect.REQUIRE_APPROVAL
        and d.matched_rule == "approve-medium-risk-mutations"
    )
    # low-risk mutation in development is autonomous
    d = engine.evaluate(ctx(tool_name="rotate_connection_pool", tool_risk=RiskLevel.LOW))
    assert (
        d.effect is PolicyEffect.ALLOW and d.matched_rule == "allow-low-risk-mutations-development"
    )
    # ...but not in production
    d = engine.evaluate(
        ctx(
            tool_name="rotate_connection_pool",
            tool_risk=RiskLevel.LOW,
            environment=Environment.PRODUCTION,
        )
    )
    assert d.effect is PolicyEffect.REQUIRE_APPROVAL
    # rollback always requires approval
    d = engine.evaluate(ctx(tool_name="rollback_deployment", tool_risk=RiskLevel.MEDIUM))
    assert d.matched_rule == "approve-rollback"
    # SEV1 low-risk mutation still requires approval
    d = engine.evaluate(
        ctx(tool_name="rotate_connection_pool", tool_risk=RiskLevel.LOW, severity=Severity.SEV1)
    )
    assert d.effect is PolicyEffect.REQUIRE_APPROVAL
    # high risk in production is denied
    d = engine.evaluate(ctx(tool_risk=RiskLevel.HIGH, environment=Environment.PRODUCTION))
    assert d.effect is PolicyEffect.DENY and d.matched_rule == "deny-high-risk-mutation-production"


def test_invariants_cannot_be_overridden() -> None:
    permissive = PolicyEngine(
        [PolicyRule(name="allow-all", priority=10_000, effect=PolicyEffect.ALLOW)]
    )
    d = permissive.evaluate(
        ctx(
            tool_name="drop_database",
            tool_category=ToolCategory.DANGEROUS,
            tool_risk=RiskLevel.CRITICAL,
        )
    )
    assert d.effect is PolicyEffect.DENY and d.invariant == "dangerous_tools_denied"
    d = permissive.evaluate(ctx(in_agent_loop=True, actor=Actor.agent("r")))
    assert d.effect is PolicyEffect.DENY and d.invariant == "no_mutation_inside_agent_loop"
    d = permissive.evaluate(ctx(in_agent_loop=False, actor=Actor.agent("r")))
    assert d.effect is PolicyEffect.DENY and d.invariant == "agent_cannot_mutate"
    # a legitimate workflow-executed mutation passes the invariants and hits the rule
    assert permissive.evaluate(ctx()).effect is PolicyEffect.ALLOW


def test_default_deny_when_nothing_matches() -> None:
    engine = PolicyEngine(
        [
            PolicyRule(
                name="only-reads",
                effect=PolicyEffect.ALLOW,
                categories=frozenset({ToolCategory.READ_ONLY}),
            )
        ]
    )
    d = engine.evaluate(ctx())
    assert d.effect is PolicyEffect.DENY and d.matched_rule is None
    assert "default is deny" in d.reason


def test_priority_order_and_role_requirement() -> None:
    engine = PolicyEngine(
        [
            PolicyRule(name="low", priority=1, effect=PolicyEffect.ALLOW),
            PolicyRule(
                name="high",
                priority=9,
                effect=PolicyEffect.DENY,
                tools=frozenset({"restart_service"}),
            ),
            PolicyRule(
                name="admin-only",
                priority=5,
                effect=PolicyEffect.ALLOW,
                required_role=Role.ADMIN,
                tools=frozenset({"scale_service"}),
            ),
        ]
    )
    assert engine.evaluate(ctx()).matched_rule == "high"
    d = engine.evaluate(
        ctx(tool_name="scale_service", actor=Actor.human("v", frozenset({Role.VIEWER})))
    )
    assert d.matched_rule == "low"  # admin-only skipped, falls through to low
    d = engine.evaluate(
        ctx(tool_name="scale_service", actor=Actor.human("a", frozenset({Role.ADMIN})))
    )
    assert d.matched_rule == "admin-only"


def test_loader_rejects_invalid_rules() -> None:
    with pytest.raises(PolicyDefinitionError):
        parse_rule({"name": "x", "effect": "maybe"})
    with pytest.raises(PolicyDefinitionError):
        parse_rule({"effect": "allow"})
    with pytest.raises(PolicyDefinitionError):
        load_policy_dir(Path("/nonexistent"))
