"""LangGraph state. Deliberately small: large data (evidence, hypotheses) lives in the database and
is re-read each iteration, so checkpoints stay cheap and resumable."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    incident_id: str
    agent_run_id: str
    flow_name: str
    flow_version: str
    phase: str
    iteration: int
    usage: dict[str, Any]
    feedback: list[str]
    decision: (
        str | None
    )  # continue | transition | terminate | action_planned | no_action | escalate
    trigger: str | None
    next_phase: str | None
    termination: str | None
    action_plan_id: str | None
    llm_available: bool
    invalid_streak: int
    last_proposal: dict[str, Any] | None
    step_seq: int
    summary: str
    model: str
    recovered: bool
    metric_lines: list[str]
    ticked_at: str | None  # ISO timestamp of the last observe, for the runtime budget
    refresh: bool  # this run follows a wait: take a measurement before judging exit conditions
    evidence_seen: int  # evidence count at the previous iteration, to detect a stalled phase
    stalled_streak: int


def initial_state(
    *,
    incident_id: str,
    agent_run_id: str,
    flow_name: str,
    flow_version: str,
    phase: str,
    usage: dict[str, Any],
    llm_available: bool,
    feedback: list[str] | None = None,
    ticked_at: str | None = None,
    refresh: bool = False,
) -> AgentState:
    return AgentState(
        incident_id=incident_id,
        agent_run_id=agent_run_id,
        flow_name=flow_name,
        flow_version=flow_version,
        phase=phase,
        iteration=0,
        usage=usage,
        feedback=list(feedback or []),
        decision=None,
        trigger=None,
        next_phase=None,
        termination=None,
        action_plan_id=None,
        llm_available=llm_available,
        invalid_streak=0,
        last_proposal=None,
        step_seq=0,
        summary="",
        model="",
        recovered=False,
        metric_lines=[],
        ticked_at=ticked_at,
        refresh=refresh,
        evidence_seen=-1,
        stalled_streak=0,
    )
