"""Unit tests for the workflow's pure decision helpers.

The full lifecycle is covered in `tests/workflow`; these exercise the guards that bound a
pathological investigation, which are awkward to provoke through a real flow.
"""

from __future__ import annotations

import uuid

import pytest
from temporalio.service import RPCError, RPCStatusCode

from aegis.domain.errors import ConflictError
from aegis.infrastructure.temporal.controller import TemporalWorkflowController
from aegis.workflows.contracts import ApprovalSignal, IncidentWorkflowInput, WorkflowStatus
from aegis.workflows.incident_workflow import (
    MAX_EARLY_DECISIONS,
    MAX_PHASE_ENTRIES,
    PHASE_STATUS,
    IncidentWorkflow,
)


def test_cycling_guard_allows_one_revisit_and_then_escalates() -> None:
    wf = IncidentWorkflow()
    assert not wf._cycling("investigate")  # never entered
    wf._status.phases.extend(["triage", "investigate", "hypothesize"])
    assert not wf._cycling("investigate")  # a second pass is productive
    wf._status.phases.append("investigate")
    assert wf._cycling("investigate")  # a third would be a loop
    assert not wf._cycling("validate")  # unrelated phases are unaffected
    assert MAX_PHASE_ENTRIES == 2


def test_remediation_may_be_replanned_once_within_the_guard() -> None:
    """A rejected plan sends the flow back to remediate; the guard must not block the retry that
    `max_remediation_attempts` allows."""
    wf = IncidentWorkflow()
    wf._status.phases.extend(["triage", "investigate", "hypothesize", "validate", "remediate"])
    assert not wf._cycling("remediate")
    wf._status.phases.append("remediate")
    assert wf._cycling("remediate")


def test_approval_signal_is_ignored_unless_it_matches_the_awaited_request() -> None:
    """A signal for a stale or unrelated approval must not release the wait."""
    wf = IncidentWorkflow()
    awaited, other = uuid.uuid4(), uuid.uuid4()
    wf._status.awaiting_approval_id = awaited
    wf.approval_decided(
        ApprovalSignal(approval_id=other, approved=True, decided_by="user:attacker", reason="")
    )
    assert wf._take_approval() is None
    wf.approval_decided(
        ApprovalSignal(approval_id=awaited, approved=True, decided_by="user:ops", reason="go")
    )
    signal = wf._take_approval()
    assert signal is not None and signal.decided_by == "user:ops"


def test_cancel_signal_is_recorded_in_the_queryable_status() -> None:
    wf = IncidentWorkflow()
    assert wf.status() == WorkflowStatus(phases=[])
    wf.cancel("closed by operator")
    assert wf.status().cancelled is True
    wf.cancel("")
    assert wf.status().cancelled is True  # an empty reason still cancels


def test_phase_status_map_only_covers_investigative_phases() -> None:
    """Phases that the workflow drives explicitly (remediate, verify) must not be remapped here."""
    assert set(PHASE_STATUS) == {"investigate", "hypothesize", "validate"}
    assert PHASE_STATUS["validate"] == "validating"
    assert IncidentWorkflowInput(incident_id=uuid.uuid4()).max_phases >= MAX_PHASE_ENTRIES * 3


def test_a_decision_that_arrives_before_the_workflow_knows_the_id_is_not_lost() -> None:
    """The API can record a human's approval before `evaluate_policy` has returned the approval id
    to the workflow. Dropping that signal would expire an approval a human had already granted."""
    wf = IncidentWorkflow()
    approval_id = uuid.uuid4()
    wf.approval_decided(
        ApprovalSignal(approval_id=approval_id, approved=True, decided_by="user:ops", reason="go")
    )
    assert wf._take_approval() is None  # not awaited yet, so it cannot release a wait
    wf._adopt_pending(approval_id)
    signal = wf._take_approval()
    assert signal is not None and signal.approved and signal.decided_by == "user:ops"
    # a second wait on an id nobody decided starts clean
    wf._adopt_pending(uuid.uuid4())
    assert wf._take_approval() is None


def test_the_early_decision_buffer_is_bounded() -> None:
    wf = IncidentWorkflow()
    ids = [uuid.uuid4() for _ in range(MAX_EARLY_DECISIONS + 4)]
    for approval_id in ids:
        wf.approval_decided(
            ApprovalSignal(approval_id=approval_id, approved=True, decided_by="user:ops", reason="")
        )
    assert len(wf._early_decisions) == MAX_EARLY_DECISIONS
    wf._adopt_pending(ids[-1])  # the most recent decision survives
    assert wf._take_approval() is not None


class _GoneHandle:
    """A workflow handle whose signals fail the way Temporal fails for a completed workflow."""

    def __init__(self) -> None:
        self.attempts = 0

    async def signal(self, *_args: object, **_kwargs: object) -> None:
        self.attempts += 1
        raise RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")


def _controller(handle: _GoneHandle) -> TemporalWorkflowController:
    controller = TemporalWorkflowController.__new__(TemporalWorkflowController)
    controller.handle = lambda _workflow_id: handle  # type: ignore[method-assign,assignment]
    return controller


async def test_closing_an_incident_whose_workflow_finished_is_not_an_error() -> None:
    """An operator closing a handed-over incident must not be blocked by the workflow being gone;
    the incident record, not the workflow, is the authoritative status."""
    handle = _GoneHandle()
    await _controller(handle).signal_cancel("incident-1", "closed by operator")
    assert handle.attempts == 1


async def test_a_decision_on_a_finished_workflow_is_reported_as_a_conflict() -> None:
    """Unlike cancel, an approval that nothing can act on must not look like it succeeded."""
    with pytest.raises(ConflictError, match="already finished"):
        await _controller(_GoneHandle()).signal_approval(
            "incident-1", uuid.uuid4(), True, "user:ops", "go"
        )
