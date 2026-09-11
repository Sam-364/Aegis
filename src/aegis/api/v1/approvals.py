from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from aegis.api.deps import Container, CurrentActor, Operator
from aegis.api.schemas import ApprovalDecision
from aegis.domain.enums import ApprovalStatus

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(
    c: Container,
    _: CurrentActor,
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items = (
        await c.approvals.list_all(limit=limit)
        if status is None
        else [a for a in await c.approvals.list_all(limit=limit) if a.status is status]
    )
    return [a.model_dump(mode="json") for a in items]


@router.get("/{approval_id}")
async def get_approval(approval_id: uuid.UUID, c: Container, _: CurrentActor) -> dict[str, Any]:
    approval = await c.approvals.get(approval_id)
    async with c.uow_factory() as uow:
        plan = await uow.action_plans.get(approval.action_plan_id)
        incident = await uow.incidents.get(approval.incident_id)
        hypothesis = (
            await uow.hypotheses.get(approval.hypothesis_id) if approval.hypothesis_id else None
        )
        evidence = [await uow.evidence.get(e) for e in approval.evidence_ids[:12]]
    return {
        "approval": approval.model_dump(mode="json"),
        "action_plan": plan.model_dump(mode="json"),
        "incident": {
            "id": str(incident.id),
            "display_id": incident.display_id,
            "title": incident.title,
            "severity": incident.severity.value,
            "status": incident.status.value,
        },
        "hypothesis": hypothesis.model_dump(mode="json") if hypothesis else None,
        "evidence": [e.model_dump(mode="json") for e in evidence],
    }


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: uuid.UUID, body: ApprovalDecision, c: Container, actor: Operator
) -> dict[str, Any]:
    return (
        await c.approvals.decide(approval_id, approved=True, actor=actor, reason=body.reason)
    ).model_dump(mode="json")


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: uuid.UUID, body: ApprovalDecision, c: Container, actor: Operator
) -> dict[str, Any]:
    return (
        await c.approvals.decide(approval_id, approved=False, actor=actor, reason=body.reason)
    ).model_dump(mode="json")
