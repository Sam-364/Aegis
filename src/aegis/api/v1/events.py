from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from aegis.api.deps import Container, CurrentActor

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def global_stream(request: Request, c: Container, _: CurrentActor) -> EventSourceResponse:
    """Live firehose of incident events across all incidents (no replay)."""

    async def generator() -> Any:
        if c.publisher is None:
            return
        async for ev in c.publisher.subscribe(None):
            if await request.is_disconnected():
                break
            yield ServerSentEvent(
                data=ev.model_dump_json(), event=ev.type, id=f"{ev.incident_id}:{ev.seq}"
            )

    return EventSourceResponse(generator(), ping=15)
