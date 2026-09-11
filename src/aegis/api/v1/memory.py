from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from aegis.api.deps import Container, CurrentActor
from aegis.domain.memory import SimilarIncidentQuery

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("")
async def list_memories(
    c: Container, _: CurrentActor, limit: int = Query(default=50, ge=1, le=200), offset: int = 0
) -> dict[str, Any]:
    async with c.uow_factory() as uow:
        items, total = await uow.memories.list_memories(limit=limit, offset=offset)
    return {
        "items": [m.model_dump(mode="json", exclude={"embedding"}) for m in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
async def search(
    c: Container,
    _: CurrentActor,
    q: str = Query(min_length=2, max_length=500),
    services: str = "",
    limit: int = Query(default=5, ge=1, le=20),
) -> list[dict[str, Any]]:
    matches = await c.memory_search(
        SimilarIncidentQuery(
            text=q, affected_services=[s for s in services.split(",") if s], limit=limit
        )
    )
    return [
        {
            "similarity": m.similarity,
            "matched_on": m.matched_on,
            "memory": m.memory.model_dump(mode="json", exclude={"embedding"}),
        }
        for m in matches
    ]
