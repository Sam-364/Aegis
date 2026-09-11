from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from aegis.api.deps import Container, CurrentActor

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    c: Container, _: CurrentActor, unread_only: bool = False, limit: int = 50
) -> list[dict[str, Any]]:
    return [
        n.model_dump(mode="json")
        for n in await c.notifications.list_notifications(unread_only=unread_only, limit=limit)
    ]


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: uuid.UUID, c: Container, _: CurrentActor) -> None:
    await c.notifications.mark_read(notification_id)
