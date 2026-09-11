"""Explicit incident state machine.

Transitions are a closed table. The LLM never touches this; only the workflow, the API (for
human actions) and the detector may request transitions, and each request is validated.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from aegis.domain.base import Actor
from aegis.domain.clock import utcnow
from aegis.domain.enums import ActorKind, IncidentStatus
from aegis.domain.errors import InvalidTransitionError
from aegis.domain.incident import Incident

S = IncidentStatus

TRANSITIONS: Final[Mapping[IncidentStatus, frozenset[IncidentStatus]]] = {
    S.DETECTED: frozenset({S.TRIAGING, S.CLOSED}),
    S.TRIAGING: frozenset({S.INVESTIGATING, S.VERIFYING, S.ESCALATED, S.CLOSED, S.FAILED}),
    S.INVESTIGATING: frozenset({S.HYPOTHESIS_FORMED, S.VERIFYING, S.ESCALATED, S.FAILED, S.CLOSED}),
    S.HYPOTHESIS_FORMED: frozenset(
        {S.VALIDATING, S.INVESTIGATING, S.ESCALATED, S.FAILED, S.CLOSED, S.VERIFYING}
    ),
    S.VALIDATING: frozenset(
        {
            S.REMEDIATION_PLANNED,
            S.HYPOTHESIS_FORMED,
            S.INVESTIGATING,
            S.VERIFYING,
            S.ESCALATED,
            S.FAILED,
            S.CLOSED,
        }
    ),
    S.REMEDIATION_PLANNED: frozenset(
        {
            S.AWAITING_APPROVAL,
            S.REMEDIATING,
            S.ESCALATED,
            S.FAILED,
            S.INVESTIGATING,
            S.CLOSED,
            S.VERIFYING,
        }
    ),
    S.AWAITING_APPROVAL: frozenset(
        {S.REMEDIATING, S.REMEDIATION_PLANNED, S.ESCALATED, S.FAILED, S.CLOSED, S.VERIFYING}
    ),
    S.REMEDIATING: frozenset({S.VERIFYING, S.ROLLED_BACK, S.ESCALATED, S.FAILED, S.CLOSED}),
    S.VERIFYING: frozenset(
        {
            S.RESOLVED,
            S.ROLLED_BACK,
            S.REMEDIATION_PLANNED,
            S.INVESTIGATING,
            S.ESCALATED,
            S.FAILED,
            S.CLOSED,
        }
    ),
    S.ROLLED_BACK: frozenset(
        {S.REMEDIATION_PLANNED, S.INVESTIGATING, S.ESCALATED, S.FAILED, S.CLOSED, S.VERIFYING}
    ),
    S.RESOLVED: frozenset({S.CLOSED, S.INVESTIGATING}),
    S.ESCALATED: frozenset({S.INVESTIGATING, S.RESOLVED, S.CLOSED, S.REMEDIATION_PLANNED}),
    S.FAILED: frozenset({S.CLOSED, S.ESCALATED, S.INVESTIGATING}),
    S.CLOSED: frozenset(),
}

# Transitions a human may request through the API. Everything else is runtime-only.
HUMAN_TRANSITIONS: Final[frozenset[tuple[IncidentStatus, IncidentStatus]]] = frozenset(
    {
        # an operator may force-close any incident (the workflow is cancelled)
        *((status, S.CLOSED) for status in S if status is not S.CLOSED),
        (S.ESCALATED, S.RESOLVED),
        (S.ESCALATED, S.INVESTIGATING),
        (S.RESOLVED, S.INVESTIGATING),
    }
)


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def assert_transition(current: IncidentStatus, target: IncidentStatus, actor: Actor) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"Cannot transition incident from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value, "actor": actor.id},
        )
    if actor.kind is ActorKind.HUMAN and (current, target) not in HUMAN_TRANSITIONS:
        raise InvalidTransitionError(
            f"Transition {current.value} -> {target.value} is reserved for the runtime",
            details={"from": current.value, "to": target.value, "actor": actor.id},
        )
    if actor.kind is ActorKind.AGENT:
        raise InvalidTransitionError(
            "The agent may not change incident status directly",
            details={"from": current.value, "to": target.value, "actor": actor.id},
        )


def transition(
    incident: Incident,
    target: IncidentStatus,
    actor: Actor,
    *,
    now: datetime | None = None,
) -> IncidentStatus:
    """Apply a validated transition, updating lifecycle timestamps. Returns the previous status."""
    previous = incident.status
    assert_transition(previous, target, actor)
    ts = now or utcnow()
    incident.status = target
    if target is S.TRIAGING and incident.acknowledged_at is None:
        incident.acknowledged_at = ts
    if target is S.RESOLVED:
        incident.resolved_at = ts
    if target is S.CLOSED:
        incident.closed_at = ts
        if incident.resolved_at is None and previous is S.RESOLVED:
            incident.resolved_at = ts
    if target is S.INVESTIGATING and previous in (S.RESOLVED, S.ESCALATED, S.FAILED):
        incident.resolved_at = None
        incident.closed_at = None
    incident.touch(ts)
    return previous
