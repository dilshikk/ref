# middlewares/group_activity.py
"""
Запоминает, кто пишет в основную группу (#18): сообщение в группе — один из
признаков живого участника при проверке «добавил N участников».

Это outer-middleware, а не обработчик: обработчик в aiogram 3 забирает событие
себе, и остальные роутеры его уже не видят.
"""
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from loguru import logger

from data.config import GROUP_ID
from loader import db

# Пишем в БД не чаще раза в 10 минут на участника
_TOUCH_COOLDOWN_SECONDS = 600.0
_MAX_CACHE_SIZE = 50_000
_last_touch: dict[int, float] = {}


def _should_touch(user_id: int, now: float) -> bool:
    last = _last_touch.get(user_id)
    return last is None or now - last >= _TOUCH_COOLDOWN_SECONDS


class GroupActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.id == GROUP_ID and event.chat.type in ("group", "supergroup"):
            user = event.from_user
            now = time.monotonic()
            if user is not None and not user.is_bot and _should_touch(user.id, now):
                try:
                    await db.touch_group_activity(GROUP_ID, user.id)
                    if len(_last_touch) > _MAX_CACHE_SIZE:
                        _last_touch.clear()
                    _last_touch[user.id] = now
                except Exception as e:
                    logger.warning(f"Не удалось записать активность в группе user={user.id}: {e}")
        return await handler(event, data)
