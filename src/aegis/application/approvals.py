"""Human approvals: decide, record, signal the workflow."""

from __future__ import annotations

import uuid

from aegis.application.events import Emitter
from aegis.domain.approval import ApprovalRequest
from aegis.domain.base import Actor
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import ActorKind, ApprovalStatus, Role
from aegis.domain.errors import ConflictError, ForbiddenError
from aegis.domain.events import EventType
from aegis.logging import get_logger
from aegis.ports.messaging import EventPublisher, WorkflowController
from aegis.ports.repositories import UnitOfWorkFactory
from aegis.telemetry.metrics import APPROVAL_WAIT_SECONDS, APPROVALS_TOTAL

log = get_logger(__name__)


class ApprovalService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        publisher: EventPublisher | None = None,
        workflows: WorkflowController | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.emitter = Emitter(publisher)
        self.workflows = workflows
        self.clock = clock or SystemClock()

    async def list_pending(self, incident_id: uuid.UUID | None = None) -> list[ApprovalRequest]:
        async with self.uow_factory() as uow:
            return await uow.approvals.list_approvals(
                status=ApprovalStatus.PENDING, incident_id=incident_id
            )

    async def list_all(
        self, incident_id: uuid.UUID | None = None, limit: int = 100
    ) -> list[ApprovalRequest]:
        async with self.uow_factory() as uow:
            return await uow.approvals.list_approvals(incident_id=incident_id, limit=limit)

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        async with self.uow_factory() as uow:
            return await uow.approvals.get(approval_id)

    async def decide(
        self, approval_id: uuid.UUID, *, approved: bool, actor: Actor, reason: str = ""
    ) -> ApprovalRequest:
        if actor.kind is ActorKind.AGENT:
            raise ForbiddenError("the agent may never approve its own actions")
        if not actor.has_role(Role.OPERATOR):
            raise ForbiddenError("approving remediation requires the operator role")
        now = self.clock.now()
        async with self.uow_factory() as uow:
            approval = await uow.approvals.get(approval_id)
            if approval.status is not ApprovalStatus.PENDING:
                raise ConflictError(f"approval is already {approval.status.value}")
            if approval.expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                await uow.approvals.save(approval)
                await uow.commit()
                raise ConflictError("approval request has expired")
            approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            approval.decided_at = now
            approval.decided_by = actor.id
            approval.decision_reason = reason
            approval.touch(now)
            await uow.approvals.save(approval)
            APPROVALS_TOTAL.labels(approval.status.value).inc()
            APPROVAL_WAIT_SECONDS.observe(max(0.0, (now - approval.requested_at).total_seconds()))
            incident = await uow.incidents.get(approval.incident_id)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.APPROVAL_DECIDED,
                actor=actor,
                title=f"Remediation {'approved' if approved else 'rejected'} by "
                f"{actor.display_name or actor.id}",
                payload={
                    "approval_id": str(approval.id),
                    "action_plan_id": str(approval.action_plan_id),
                    "approved": approved,
                    "reason": reason,
                },
            )
            await self.emitter.audit(
                uow,
                event_type="approval.decided",
                actor=actor,
                incident_id=incident.id,
                action=approval.title,
                decision="approved" if approved else "rejected",
                reason=reason,
                data={"approval_id": str(approval.id)},
            )
            workflow_id = incident.workflow_id
            await uow.commit()
        if self.workflows is not None and workflow_id:
            await self.workflows.signal_approval(
                workflow_id, approval.id, approved, actor.id, reason
            )
        log.info("approval.decided", approval_id=str(approval.id), approved=approved, by=actor.id)
        return approval
