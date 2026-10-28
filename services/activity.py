from __future__ import annotations

from typing import Any

from repositories.activity import activity_repository


class ActivityService:
    async def record(
        self,
        *,
        user_id: int,
        event: str,
        chat_id: int | None = None,
        session_id: str | None = None,
        payload: Any = None,
    ) -> None:
        await activity_repository.create(
            user_id=user_id,
            event=event,
            chat_id=chat_id,
            session_id=session_id,
            payload=payload,
        )

    async def dashboard(self) -> dict[str, int | float]:
        today_active = await activity_repository.count_active_users_since(1)
        week_active = await activity_repository.count_active_users_since(7)
        month_active = await activity_repository.count_active_users_since(30)
        new_today = await activity_repository.count_new_users_since(1)
        new_week = await activity_repository.count_new_users_since(7)
        new_month = await activity_repository.count_new_users_since(30)
        returning_today = await activity_repository.count_returning_users_since(1)
        returning_week = await activity_repository.count_returning_users_since(7)
        returning_month = await activity_repository.count_returning_users_since(30)
        retention = (returning_week / week_active * 100) if week_active else 0.0

        return {
            "dau": today_active,
            "wau": week_active,
            "mau": month_active,
            "new_today": new_today,
            "new_week": new_week,
            "new_month": new_month,
            "returning_today": returning_today,
            "returning_week": returning_week,
            "returning_month": returning_month,
            "retention": round(retention, 2),
        }


activity_service = ActivityService()
