from __future__ import annotations

from typing import Any

from loader import db


class ActivityRepository:
    async def create(
        self,
        *,
        user_id: int,
        event: str,
        chat_id: int | None = None,
        session_id: str | None = None,
        payload: Any = None,
    ) -> None:
        await db.insert_user_activity(
            user_id=user_id,
            event=event,
            chat_id=chat_id,
            session_id=session_id,
            payload=payload,
        )

    async def count_active_users_since(self, days: int) -> int:
        return await db.count_active_users_since(days)

    async def count_new_users_since(self, days: int) -> int:
        return await db.count_new_users_since(days)

    async def count_returning_users_since(self, days: int) -> int:
        return await db.count_returning_users_since(days)

    async def count_activity_events_since(self, days: int) -> int:
        return await db.count_activity_events_since(days)


activity_repository = ActivityRepository()
