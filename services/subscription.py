# services/subscription.py
"""Проверка обязательной подписки на каналы из БД."""

import time
from typing import Any, cast

from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from loader import db, bot

# Кэшируем только положительный результат: только что подписавшийся пользователь
# проверяется заново сразу, а подписанные не дёргают Telegram API на каждое сообщение
_POSITIVE_CACHE_TTL_SECONDS = 60.0
_subscribed_cache: dict[int, float] = {}


def _is_member_status(member: Any) -> bool:
    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return True
    # Ограниченный участник всё ещё состоит в канале
    return status == "restricted" and bool(getattr(member, "is_member", False))


async def is_user_subscribed(user_id: int, use_cache: bool = True) -> bool:
    now = time.monotonic()
    if use_cache:
        cached_at = _subscribed_cache.get(user_id)
        if cached_at is not None and now - cached_at < _POSITIVE_CACHE_TTL_SECONDS:
            return True

    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    for channel in channels:
        channel_id = channel["chat_id"]
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        except TelegramBadRequest as e:
            # Канал настроен неверно (бот не админ / канал удалён) — не блокируем всех пользователей
            logger.warning(f"Не удалось проверить подписку на {channel_id}: {e}")
            continue
        except Exception as e:
            logger.error(f"Ошибка проверки подписки {channel_id}: {e}")
            continue
        if not _is_member_status(member):
            _subscribed_cache.pop(user_id, None)
            return False

    _subscribed_cache[user_id] = now
    if len(_subscribed_cache) > 50_000:
        _subscribed_cache.clear()
    return True
