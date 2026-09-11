from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from aegis.api.deps import Container, CurrentActor, Operator
from aegis.api.schemas import IncidentSummary, Page, StatsResponse, StatusChange
from aegis.domain.enums import ApprovalStatus, IncidentStatus, Severity
from aegis.domain.incident import Incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentSummary])
async def list_incidents(
    c: Container,
    _: CurrentActor,
    status: Annotated[list[IncidentStatus] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    active: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[IncidentSummary]:
    items, total = await c.incidents.list_incidents(
        status=status, severity=severity, active_only=active, limit=limit, offset=offset
    )
    return Page(
        items=[IncidentSummary.from_incident(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=StatsResponse)
async def stats(c: Container, _actor: CurrentActor) -> StatsResponse:
    s = await c.incidents.stats()
    pending = await c.approvals.list_pending()
    async with c.uow_factory() as uow:
        _items, memories = await uow.memories.list_memories(limit=1)
    return StatsResponse(**s, pending_approvals=len(pending), memories=memories)


@router.get("/{incident_id}")
async def get_incident(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> dict[str, Any]:
    detail = await c.incidents.detail(incident_id)
    return detail.as_dict()


@router.get("/{incident_id}/timeline")
async def timeline(
    incident_id: uuid.UUID,
    c: Container,
    _: CurrentActor,
    after_seq: int = 0,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    return [
        e.model_dump(mode="json") for e in await c.incidents.timeline(incident_id, after_seq, limit)
    ]


@router.get("/{incident_id}/evidence")
async def evidence(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> dict[str, Any]:
    return (await c.incidents.evidence(incident_id)).model_dump(mode="json")


@router.get("/{incident_id}/hypotheses")
async def hypotheses(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [h.model_dump(mode="json") for h in await c.incidents.hypotheses(incident_id)]


@router.get("/{incident_id}/actions")
async def actions(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in await c.incidents.actions(incident_id)]


@router.get("/{incident_id}/approvals")
async def approvals(
    incident_id: uuid.UUID, c: Container, _: CurrentActor, status: ApprovalStatus | None = None
) -> list[dict[str, Any]]:
    items = await c.approvals.list_all(incident_id)
    return [a.model_dump(mode="json") for a in items if status is None or a.status is status]


@router.get("/{incident_id}/audit")
async def audit(
    incident_id: uuid.UUID,
    c: Container,
    _: CurrentActor,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = 0,
) -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in await c.incidents.audit(incident_id, limit, offset)]


@router.get("/{incident_id}/agent-runs")
async def agent_runs(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in await c.incidents.agent_runs(incident_id)]


@router.get("/{incident_id}/tool-executions")
async def tool_executions(
    incident_id: uuid.UUID, c: Container, _: CurrentActor
) -> list[dict[str, Any]]:
    return [t.model_dump(mode="json") for t in await c.incidents.tool_executions(incident_id)]


@router.get("/{incident_id}/workflow")
async def workflow(incident_id: uuid.UUID, c: Container, _: CurrentActor) -> dict[str, Any]:
    incident = await c.incidents.get(incident_id)
    if c.workflows is None or not incident.workflow_id:
        return {"workflow_id": incident.workflow_id, "status": None}
    return await c.workflows.describe(incident.workflow_id)


@router.post("/{incident_id}/acknowledge")
async def acknowledge(incident_id: uuid.UUID, c: Container, actor: Operator) -> IncidentSummary:
    return IncidentSummary.from_incident(await c.incidents.acknowledge(incident_id, actor))


@router.post("/{incident_id}/close")
async def close(
    incident_id: uuid.UUID, body: StatusChange, c: Container, actor: Operator
) -> IncidentSummary:
    return IncidentSummary.from_incident(
        await c.incidents.change_status(incident_id, IncidentStatus.CLOSED, actor, body.reason)
    )


@router.post("/{incident_id}/resolve")
async def resolve(
    incident_id: uuid.UUID, body: StatusChange, c: Container, actor: Operator
) -> IncidentSummary:
    return IncidentSummary.from_incident(
        await c.incidents.change_status(incident_id, IncidentStatus.RESOLVED, actor, body.reason)
    )


@router.post("/{incident_id}/reopen")
async def reopen(
    incident_id: uuid.UUID, body: StatusChange, c: Container, actor: Operator
) -> IncidentSummary:
    return IncidentSummary.from_incident(
        await c.incidents.change_status(
            incident_id, IncidentStatus.INVESTIGATING, actor, body.reason
        )
    )


@router.get("/{incident_id}/stream")
async def stream(
    incident_id: uuid.UUID, request: Request, c: Container, _: CurrentActor, after_seq: int = 0
) -> EventSourceResponse:
    """Ordered live timeline: replays events after ``after_seq`` (or ``Last-Event-ID``), then
    follows live events."""
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        after_seq = max(after_seq, int(last_event_id))
    await c.incidents.get(incident_id)

    async def generator() -> Any:
        last = after_seq
        for ev in await c.incidents.timeline(incident_id, after_seq=after_seq, limit=2000):
            last = max(last, ev.seq)
            yield ServerSentEvent(data=ev.model_dump_json(), event=ev.type, id=str(ev.seq))
        if c.publisher is None:
            return
        async for ev in c.publisher.subscribe(incident_id):
            if await request.is_disconnected():
                break
            if ev.seq <= last:
                continue
            last = ev.seq
            yield ServerSentEvent(data=ev.model_dump_json(), event=ev.type, id=str(ev.seq))

    return EventSourceResponse(generator(), ping=15)


def _unused(_: Incident) -> None:
    return None
