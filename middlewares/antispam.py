import asyncio
import time

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from loguru import logger


class SmartRateLimiter(BaseMiddleware):
    """
    💎 Умный антиспам для Aiogram 3.x
    - Работает с Message и CallbackQuery
    - Игнорирует дубликаты
    - Самоочищается
    - Отдельная логика для /start
    """

    def __init__(self, limit: float = 2.0, start_limit: float = 5.0):
        super().__init__()
        self.limit = limit
        self.start_limit = start_limit
        self.user_last_action: dict[int, float] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.cleanup_interval = 300
        self.last_cleanup = time.time()

    async def __call__(self, handler, event, data):
        now = time.time()

        if isinstance(event, Message):
            user_id = event.from_user.id
            text = event.text or ""
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            text = event.data or ""
        else:
            return await handler(event, data)

        if now - self.last_cleanup > self.cleanup_interval:
            self._cleanup_old(now)

        limit = self.start_limit if text.strip().startswith("/start") else self.limit
        last_time = self.user_last_action.get(user_id, 0)

        if now - last_time < limit:
            logger.debug(f"🚫 Спам от {user_id}: {text}")
            return

        self.user_last_action[user_id] = now

        lock = self.locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            return

        async with lock:
            return await handler(event, data)

    def _cleanup_old(self, now: float) -> None:
        for uid in list(self.user_last_action.keys()):
            if now - self.user_last_action[uid] > self.cleanup_interval:
                del self.user_last_action[uid]
        self.last_cleanup = now
        logger.info("🧹 Очистка старых записей антиспама.")
