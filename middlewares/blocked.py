# middlewares/blocked.py
"""Не пропускает к обработчикам заблокированных пользователей (кроме админов)."""

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from data.config import ADMINS
from loader import db

# Короткий кэш, чтобы не ходить в БД на каждое сообщение
_CACHE_TTL_SECONDS = 30.0


class BlockedUserMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[int, tuple[bool, float]] = {}

    async def _is_blocked(self, user_id: int) -> bool:
        now = time.monotonic()
        cached = self._cache.get(user_id)
        if cached and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
        try:
            blocked = await db.is_user_blocked(user_id)
        except Exception as e:
            logger.warning(f"Не удалось проверить блокировку user={user_id}: {e}")
            blocked = False
        self._cache[user_id] = (blocked, now)
        return blocked

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id in ADMINS:
            return await handler(event, data)

        # Действует только в личке с ботом; события групп обрабатываются отдельно
        chat = event.chat if isinstance(event, Message) else (
            event.message.chat if isinstance(event, CallbackQuery) and event.message else None
        )
        if chat is not None and chat.type != "private":
            return await handler(event, data)

        if not await self._is_blocked(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Ваш аккаунт заблокирован.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⛔ Ваш аккаунт заблокирован администратором.")
        return None
