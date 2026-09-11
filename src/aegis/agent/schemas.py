"""Strict structured-output schemas the model must produce.

Rules for these models: no free-form dicts (strict JSON schema forbids additionalProperties), so
tool arguments travel as JSON strings and are parsed/validated by the runtime against the tool's
own argument model. Every field is present in every answer.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from aegis.hypotheses.engine import HypothesisProposal

ProposalAction = Literal[
    "call_tool",
    "propose_hypotheses",
    "update_hypothesis",
    "plan_remediation",
    "conclude_no_action",
    "phase_complete",
    "escalate",
]

TestOutcomeLiteral = Literal["confirmed", "refuted", "inconclusive"]


def parse_arguments(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments_json is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("arguments_json must encode a JSON object")
    return value


class ToolCallProposal(BaseModel):
    tool_name: str
    arguments_json: str = Field(description="JSON object with the tool's arguments")
    purpose: str = Field(max_length=300, description="what this call should reveal")
    tests_hypothesis: str | None = Field(
        default=None,
        description="hypothesis handle (H1, H2…) this call is meant to confirm or refute",
    )
    expectation: str = Field(
        default="", max_length=300, description="what result would confirm the hypothesis"
    )


class HypothesisUpdateProposal(BaseModel):
    hypothesis: str = Field(description="hypothesis handle, e.g. H1")
    add_supporting_evidence: list[str] = Field(default_factory=list)
    add_contradicting_evidence: list[str] = Field(default_factory=list)
    test_outcome: TestOutcomeLiteral | None = None
    test_detail: str = Field(default="", max_length=400)
    abandon: bool = False


class RemediationProposal(BaseModel):
    tool_name: str
    arguments_json: str
    target_hypothesis: str = Field(description="handle of the confirmed hypothesis this addresses")
    reason: str = Field(max_length=500)
    expected_effect: str = Field(max_length=400)
    rollback_tool_name: str | None = None
    rollback_arguments_json: str = "{}"


class AgentProposal(BaseModel):
    """One step of the loop. Exactly one action; the runtime validates everything."""

    observation: str = Field(
        max_length=600,
        description="what the evidence currently shows, in one or two sentences "
        "(shown to operators)",
    )
    action: ProposalAction
    tool_call: ToolCallProposal | None = None
    hypotheses: list[HypothesisProposal] = Field(default_factory=list)
    hypothesis_update: HypothesisUpdateProposal | None = None
    remediation: RemediationProposal | None = None
    rationale: str = Field(max_length=500, description="why this is the right next step")

    def payload(self) -> BaseModel | list[HypothesisProposal] | None:
        match self.action:
            case "call_tool":
                return self.tool_call
            case "propose_hypotheses":
                return self.hypotheses
            case "update_hypothesis":
                return self.hypothesis_update
            case "plan_remediation":
                return self.remediation
        return None


class TestJudgement(BaseModel):
    """Fast-tier judgement of a validation diagnostic against a hypothesis."""

    outcome: TestOutcomeLiteral
    detail: str = Field(max_length=400)


class MemorySummary(BaseModel):
    """Fast-tier structured summary written to incident memory at resolution."""

    title: str = Field(max_length=120)
    symptoms: list[str] = Field(max_length=8)
    root_cause: str = Field(max_length=300)
    resolution: str = Field(max_length=300)
    lessons: list[str] = Field(max_length=5)
