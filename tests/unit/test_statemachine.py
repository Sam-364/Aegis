from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aegis.domain.base import Actor
from aegis.domain.enums import IncidentStatus as S
from aegis.domain.enums import Role, Severity
from aegis.domain.errors import InvalidTransitionError
from aegis.domain.incident import Incident
from aegis.domain.statemachine import TRANSITIONS, can_transition, transition


def make_incident(status: S = S.DETECTED) -> Incident:
    return Incident(title="t", severity=Severity.SEV2, status=status)


def test_happy_path_lifecycle() -> None:
    inc = make_incident()
    wf = Actor.workflow("wf-1")
    path = [
        S.TRIAGING,
        S.INVESTIGATING,
        S.HYPOTHESIS_FORMED,
        S.VALIDATING,
        S.REMEDIATION_PLANNED,
        S.AWAITING_APPROVAL,
        S.REMEDIATING,
        S.VERIFYING,
        S.RESOLVED,
        S.CLOSED,
    ]
    for target in path:
        transition(inc, target, wf)
        assert inc.status is target
    assert inc.resolved_at is not None
    assert inc.closed_at is not None
    assert inc.version == 1  # versioning is owned by the repository, not the domain


def test_invalid_transition_rejected() -> None:
    inc = make_incident()
    with pytest.raises(InvalidTransitionError) as exc:
        transition(inc, S.RESOLVED, Actor.workflow("wf"))
    assert exc.value.code == "invalid_transition"
    assert inc.status is S.DETECTED


def test_agent_can_never_transition() -> None:
    inc = make_incident(S.INVESTIGATING)
    with pytest.raises(InvalidTransitionError):
        transition(inc, S.HYPOTHESIS_FORMED, Actor.agent("run"))


def test_human_may_only_use_human_transitions() -> None:
    human = Actor.human("ops", frozenset({Role.OPERATOR}))
    inc = make_incident(S.RESOLVED)
    transition(inc, S.CLOSED, human)
    assert inc.status is S.CLOSED
    inc2 = make_incident(S.INVESTIGATING)
    with pytest.raises(InvalidTransitionError):
        transition(inc2, S.HYPOTHESIS_FORMED, human)


def test_closed_is_terminal() -> None:
    assert TRANSITIONS[S.CLOSED] == frozenset()
    assert S.CLOSED.is_terminal
    for status in S:
        if status is not S.CLOSED:
            assert not status.is_terminal


def test_every_status_reachable_from_detected() -> None:
    seen = {S.DETECTED}
    frontier = [S.DETECTED]
    while frontier:
        cur = frontier.pop()
        for nxt in TRANSITIONS[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(S)


def test_every_non_terminal_status_can_reach_closed() -> None:
    for start in S:
        seen = {start}
        frontier = [start]
        while frontier:
            cur = frontier.pop()
            for nxt in TRANSITIONS[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert S.CLOSED in seen, start


@given(st.sampled_from(list(S)), st.sampled_from(list(S)))
def test_can_transition_matches_table(a: S, b: S) -> None:
    assert can_transition(a, b) == (b in TRANSITIONS[a])


def test_false_positive_path_goes_through_verification() -> None:
    inc = make_incident(S.INVESTIGATING)
    wf = Actor.workflow("wf")
    transition(inc, S.VERIFYING, wf)
    transition(inc, S.RESOLVED, wf)
    assert inc.status is S.RESOLVED


def test_reopen_clears_resolution_timestamps() -> None:
    inc = make_incident(S.RESOLVED)
    human = Actor.human("ops", frozenset({Role.OPERATOR}))
    inc.resolved_at = inc.detected_at
    transition(inc, S.INVESTIGATING, human)
    assert inc.resolved_at is None


def test_spontaneous_recovery_can_be_verified_from_any_investigative_state() -> None:
    """The runtime may conclude that symptoms cleared during triage, investigation, hypothesis
    forming or validation; each must be able to reach VERIFYING and then RESOLVED."""
    wf = Actor.workflow("wf")
    for start in (
        S.TRIAGING,
        S.INVESTIGATING,
        S.HYPOTHESIS_FORMED,
        S.VALIDATING,
        S.REMEDIATION_PLANNED,
        S.AWAITING_APPROVAL,
        S.ROLLED_BACK,
    ):
        inc = make_incident(start)
        transition(inc, S.VERIFYING, wf)
        transition(inc, S.RESOLVED, wf)
        assert inc.status is S.RESOLVED and inc.resolved_at is not None
