import asyncio
import time

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from loguru import logger


class SmartRateLimiter(BaseMiddleware):
    """
    Антиспам для личных сообщений.

    - Отсекает только ПОВТОРЫ одного и того же действия (двойной тап по кнопке,
      повторный /start). Разные действия подряд (кнопка, затем ответ в форме)
      проходят — раньше второе действие молча терялось.
    - Пока обрабатывается предыдущее действие пользователя, новые ждут в очереди
      (не больше max_pending), а не выбрасываются.
    - Отброшенный callback получает ответ, чтобы кнопка не «висела» с часиками.
    - Группы не ограничиваются: там приходят события добавления участников,
      их потеря означает потерю начислений.
    """

    def __init__(self, limit: float = 1.0, start_limit: float = 5.0, max_pending: int = 3):
        super().__init__()
        self.limit = limit
        self.start_limit = start_limit
        self.max_pending = max_pending
        self.last_action: dict[int, tuple[str, float]] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.pending: dict[int, int] = {}
        self.cleanup_interval = 300
        self.last_cleanup = time.time()

    @staticmethod
    def _extract(event) -> tuple[int, str] | None:
        """Возвращает (user_id, ключ действия) для событий из лички, иначе None."""
        if isinstance(event, Message):
            if event.chat.type != "private" or not event.from_user:
                return None
            return event.from_user.id, f"m:{(event.text or '').strip()}" if event.text else f"m:#{event.message_id}"
        if isinstance(event, CallbackQuery):
            if not event.from_user:
                return None
            if event.message and event.message.chat.type != "private":
                return None
            return event.from_user.id, f"c:{event.data or ''}"
        return None

    @staticmethod
    async def _answer_dropped(event) -> None:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("⏳ Подождите секунду…")
            except TelegramBadRequest:
                pass

    async def __call__(self, handler, event, data):
        extracted = self._extract(event)
        if extracted is None:
            return await handler(event, data)

        user_id, key = extracted
        now = time.time()
        if now - self.last_cleanup > self.cleanup_interval:
            self._cleanup_old(now)

        limit = self.start_limit if key.startswith("m:/start") else self.limit
        last_key, last_time = self.last_action.get(user_id, ("", 0.0))
        if key == last_key and now - last_time < limit:
            logger.debug(f"🚫 Повтор от {user_id}: {key}")
            await self._answer_dropped(event)
            return None
        self.last_action[user_id] = (key, now)

        if self.pending.get(user_id, 0) >= self.max_pending:
            logger.debug(f"🚫 Очередь переполнена у {user_id}: {key}")
            await self._answer_dropped(event)
            return None

        lock = self.locks.setdefault(user_id, asyncio.Lock())
        self.pending[user_id] = self.pending.get(user_id, 0) + 1
        try:
            async with lock:
                return await handler(event, data)
        finally:
            self.pending[user_id] -= 1
            if self.pending[user_id] <= 0:
                self.pending.pop(user_id, None)

    def _cleanup_old(self, now: float) -> None:
        for uid in list(self.last_action.keys()):
            if now - self.last_action[uid][1] > self.cleanup_interval:
                del self.last_action[uid]
        # Удаляем только свободные блокировки, чтобы не сломать ожидающих
        for uid in list(self.locks.keys()):
            if uid not in self.pending and not self.locks[uid].locked():
                del self.locks[uid]
        self.last_cleanup = now
        logger.info("🧹 Очистка старых записей антиспама.")
