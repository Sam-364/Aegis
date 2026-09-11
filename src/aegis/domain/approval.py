"""Human approval requests. The workflow waits durably on these."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from aegis.domain.base import Timestamped
from aegis.domain.enums import ApprovalStatus, RiskLevel
from aegis.domain.ids import ActionPlanId, ApprovalId, IncidentId, new_id


class ApprovalRequest(Timestamped):
    id: ApprovalId = Field(default_factory=lambda: ApprovalId(new_id()))
    incident_id: IncidentId
    action_plan_id: ActionPlanId
    status: ApprovalStatus = ApprovalStatus.PENDING
    title: str
    summary: str
    risk: RiskLevel
    expected_impact: str = ""
    rollback_summary: str = ""
    hypothesis_id: uuid.UUID | None = None
    hypothesis_statement: str = ""
    hypothesis_confidence: float = 0.0
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    requested_by: str = "workflow"
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str = ""
    context: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING
