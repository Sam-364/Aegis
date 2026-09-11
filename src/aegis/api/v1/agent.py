from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from aegis.api.deps import Container, CurrentActor

router = APIRouter(prefix="/agent-runs", tags=["agent"])


@router.get("")
async def recent_runs(
    c: Container, _: CurrentActor, limit: int = Query(default=50, ge=1, le=200)
) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in await c.incidents.recent_agent_runs(limit)]


@router.get("/{run_id}")
async def run(run_id: uuid.UUID, c: Container, _: CurrentActor) -> dict[str, Any]:
    async with c.uow_factory() as uow:
        r = await uow.agent_runs.get(run_id)
    steps = await c.incidents.agent_steps(run_id)
    return {"run": r.model_dump(mode="json"), "steps": [s.model_dump(mode="json") for s in steps]}


@router.get("/{run_id}/steps")
async def steps(run_id: uuid.UUID, c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in await c.incidents.agent_steps(run_id)]
