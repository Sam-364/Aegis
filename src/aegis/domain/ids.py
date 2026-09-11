"""Typed identifiers.

Every aggregate has its own ``NewType`` over :class:`uuid.UUID` so that an ``IncidentId`` can
never be passed where an ``ActionPlanId`` is expected. Pydantic validates them as plain UUIDs.
"""

from __future__ import annotations

import uuid
from typing import NewType

IncidentId = NewType("IncidentId", uuid.UUID)
EvidenceId = NewType("EvidenceId", uuid.UUID)
HypothesisId = NewType("HypothesisId", uuid.UUID)
ActionPlanId = NewType("ActionPlanId", uuid.UUID)
ActionExecutionId = NewType("ActionExecutionId", uuid.UUID)
ApprovalId = NewType("ApprovalId", uuid.UUID)
AgentRunId = NewType("AgentRunId", uuid.UUID)
AgentStepId = NewType("AgentStepId", uuid.UUID)
ToolExecutionId = NewType("ToolExecutionId", uuid.UUID)
AuditEventId = NewType("AuditEventId", uuid.UUID)
SignalId = NewType("SignalId", uuid.UUID)
MemoryId = NewType("MemoryId", uuid.UUID)
PolicyDecisionId = NewType("PolicyDecisionId", uuid.UUID)
NotificationId = NewType("NotificationId", uuid.UUID)
TenantId = NewType("TenantId", str)


def new_id() -> uuid.UUID:
    """Generate a new random identifier (UUIDv4)."""
    return uuid.uuid4()


DEFAULT_TENANT = TenantId("default")
