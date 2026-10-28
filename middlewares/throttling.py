import time

from aiogram import BaseMiddleware
from aiogram.types import Message


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, slow_mode_delay: float = 0.5):
        super().__init__()
        self.user_timeouts: dict[int, float] = {}
        self.slow_mode_delay = slow_mode_delay

    async def __call__(self, handler, event: Message, data):
        user_id = event.from_user.id
        now = time.time()

        if now - self.user_timeouts.get(user_id, 0) < self.slow_mode_delay:
            await event.reply("🚫 Слишком много запросов! Пожалуйста, подождите немного.")
            return

        self.user_timeouts[user_id] = now
        return await handler(event, data)
