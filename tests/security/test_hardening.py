"""Regression tests for the findings of the adversarial security review.

Each test names the property that was violated before the fix, not the code path, so a future
refactor that reintroduces the weakness fails here rather than silently passing.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from pydantic import BaseModel, ValidationError

from aegis.agent.runtime import AgentRuntime
from aegis.config import Settings
from aegis.domain.action import VerificationSpec
from aegis.domain.enums import EvidenceKind, HypothesisCategory, VerificationStatus
from aegis.domain.evidence import Evidence
from aegis.domain.flow import BudgetUsage, ExecutionBudget
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.text import sanitize_display_text
from aegis.flow.loader import load_flow_dir
from aegis.flow.registry import FlowDefinitionError, FlowRegistry
from aegis.logging import REDACTED, redact_url, redact_value
from aegis.ports.llm import LLMUsage, StructuredResult
from aegis.verification.engine import VerificationEngine
from tests.helpers import ROOT, SimClock, build_runtime

RTL_OVERRIDE = "\u202e"  # right-to-left override
ZERO_WIDTH = "\u200b"  # zero-width space
BIDI_ISOLATE = "\u2066"  # left-to-right isolate


class Answer(BaseModel):
    statement: str
    notes: list[str] = []


def test_model_text_cannot_hide_what_it_says_from_the_operator() -> None:
    """An approving human must read the same string the runtime stored: no bidi overrides, no
    zero-width joiners, no control characters that rewrite a service name in the terminal."""
    hostile = f"restart order-service{RTL_OVERRIDE})ecivres-tnemyap( kcabllor{ZERO_WIDTH}\x07"
    result = StructuredResult[Answer](
        value=Answer(statement=hostile, notes=[f"ok{ZERO_WIDTH}{BIDI_ISOLATE}now"]),
        usage=LLMUsage(model="test"),
    )
    for forbidden in (RTL_OVERRIDE, ZERO_WIDTH, BIDI_ISOLATE, "\x07"):
        assert forbidden not in result.value.statement
        assert forbidden not in result.value.notes[0]
    assert result.value.statement.startswith("restart order-service")
    assert result.value.notes == ["oknow"]
    # plain text is untouched
    assert sanitize_display_text("restart order-service") == "restart order-service"


def test_a_key_in_a_query_string_is_redacted() -> None:
    assert redact_url("http://api/v1/events/stream?key=s3cr3tvalue&after_seq=4") == (
        f"http://api/v1/events/stream?key={REDACTED}&after_seq=4"
    )
    redacted = redact_value("psql 'password=hunter2 host=db' token=abc123")
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert redact_value("service=order-service replicas=3") == "service=order-service replicas=3"


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_authentication_cannot_be_disabled_outside_development(environment: str) -> None:
    with pytest.raises(ValidationError, match="cannot be disabled"):
        Settings(environment=environment, api_auth_mode="disabled")
    assert Settings(environment="development", api_auth_mode="disabled").api_auth_mode == "disabled"


def test_a_flow_pack_cannot_hand_a_mutating_tool_to_the_agent_loop() -> None:
    rt = build_runtime()
    packs = load_flow_dir(ROOT / "flows")
    mutating = {n for n in rt.registry.names() if rt.registry.spec(n).category.is_mutation}
    assert mutating, "expected the default registry to contain mutating tools"
    # the shipped packs are clean
    FlowRegistry(packs).validate_tools(set(rt.registry.names()), mutating)
    victim = packs[0]
    phase = next(p for p in victim.phases if p.allowed_tools)
    tampered = victim.model_copy(
        update={
            "phases": tuple(
                p.model_copy(update={"allowed_tools": p.allowed_tools | {"restart_service"}})
                if p.name == phase.name
                else p
                for p in victim.phases
            )
        }
    )
    with pytest.raises(FlowDefinitionError, match="inside the agent loop"):
        FlowRegistry([tampered]).validate_tools(set(rt.registry.names()), mutating)


async def test_verification_never_passes_without_measured_conditions() -> None:
    rt = build_runtime()
    engine = VerificationEngine(rt.telemetry, clock=SimClock(rt.engine))
    result = await engine.verify(VerificationSpec(conditions=()))
    assert result.status is VerificationStatus.INCONCLUSIVE
    assert "cannot be proven" in result.summary


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        incident_id=uuid.uuid4(),
        statement="order-service leaks redis connections",
        category=HypothesisCategory.RESOURCE_EXHAUSTION,
        suspected_root_cause_service="order-service",
    )


def _evidence(
    *, execution: uuid.UUID | None, strength: float = 0.9, tags: list[str] | None = None
) -> Evidence:
    return Evidence(
        incident_id=uuid.uuid4(),
        kind=EvidenceKind.DIAGNOSTIC,
        source="inspect_redis",
        service="order-service",
        title="connections",
        summary="order-service holds 94% of connections",
        strength=strength,
        tool_execution_id=execution,
        tags=tags or [],
    )


def test_the_model_cannot_confirm_a_hypothesis_with_evidence_it_authored() -> None:
    """Only a real tool execution confirms a test. Evidence the model attached to a hypothesis
    update carries no execution id and must not be enough."""
    target = _hypothesis()
    assert AgentRuntime._diagnostic_backing([_evidence(execution=None)], target) == []
    assert (
        AgentRuntime._diagnostic_backing(
            [_evidence(execution=uuid.uuid4(), tags=["normal"])], target
        )
        == []
    )
    assert (
        AgentRuntime._diagnostic_backing([_evidence(execution=uuid.uuid4(), strength=0.4)], target)
        == []
    )
    assert len(AgentRuntime._diagnostic_backing([_evidence(execution=uuid.uuid4())], target)) == 1


def test_wall_clock_time_is_charged_against_the_runtime_budget() -> None:
    """`max_runtime_seconds` was declared by every flow pack and incremented by nothing."""
    rt = build_runtime()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.clock = SimClock(rt.engine)
    state = {"ticked_at": rt.engine.now.isoformat(), "usage": BudgetUsage().model_dump()}
    rt.engine.advance(30)
    usage, state = runtime._tick(state, BudgetUsage())
    assert usage.runtime_seconds == pytest.approx(30, abs=1)
    assert usage.exceeded(ExecutionBudget(max_runtime_seconds=20)) == [
        f"runtime {usage.runtime_seconds:.0f}s >= 20s"
    ]
    # a worker that was down for an hour must not have the outage charged to the agent
    state = {**state, "ticked_at": (rt.engine.now - timedelta(hours=1)).isoformat()}
    resumed, _ = runtime._tick(state, usage)
    assert resumed.runtime_seconds - usage.runtime_seconds == pytest.approx(
        AgentRuntime.MAX_TICK_SECONDS, abs=1
    )
