"""In-app notifications."""

from __future__ import annotations

import uuid

from aegis.domain.notification import Notification
from aegis.ports.repositories import UnitOfWorkFactory


class NotificationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def list_notifications(
        self, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        async with self.uow_factory() as uow:
            return await uow.notifications.list_notifications(unread_only=unread_only, limit=limit)

    async def mark_read(self, notification_id: uuid.UUID) -> None:
        async with self.uow_factory() as uow:
            await uow.notifications.mark_read(notification_id)
            await uow.commit()
