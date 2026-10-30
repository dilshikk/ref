# middlewares/profile_sync.py
"""
Обновляет имя и username пользователя в базе, когда он меняет их в Telegram.
Раньше они записывались только при первом /start, и админ видел старый username.

Outer-middleware: видит каждое сообщение и нажатие кнопки и не забирает его
у обработчиков. В базу пишем только когда данные отличаются от уже записанных
(кэш в памяти), а сам UPDATE ничего не меняет, если в базе уже то же самое.
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger

from loader import db
from utils.profile_sync import ProfileCache, normalize


async def _save_profile(telegram_id: int, full_name: str, username: str | None) -> None:
    # Только для уже зарегистрированных: регистрация по-прежнему в /start
    await db.execute(
        """
        UPDATE Users SET full_name = $2, username = $3
        WHERE telegram_id = $1
          AND (full_name IS DISTINCT FROM $2 OR username IS DISTINCT FROM $3)
        """,
        parameters=(telegram_id, full_name, username),
    )


class ProfileSyncMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._cache = ProfileCache()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and not user.is_bot:
            key = normalize(user.full_name, user.username)
            # Пустое имя не записываем: колонка full_name обязательная
            if key[0] and self._cache.changed(user.id, key):
                try:
                    await _save_profile(user.id, *key)
                    self._cache.remember(user.id, key)
                except Exception as e:
                    # Сбой обновления профиля не должен мешать ответу пользователю
                    logger.warning(f"Не удалось обновить имя/username user={user.id}: {e}")
        return await handler(event, data)
