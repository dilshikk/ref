# services/subscription.py
"""Проверка обязательной подписки на каналы из БД."""

import time
from typing import Any, cast

from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from loader import db, bot

# Положительный результат держим дольше: подписанные не дёргают Telegram API
# на каждое сообщение.
_POSITIVE_CACHE_TTL_SECONDS = 60.0
# Отрицательный — коротко: неподписанный, который шлёт много сообщений подряд,
# больше не вызывает N запросов get_chat_member на каждое (риск лимитов Telegram).
# Кнопка «Я подписался» и /start проверяют без кэша, поэтому только что
# подписавшийся человек не ждёт.
_NEGATIVE_CACHE_TTL_SECONDS = 15.0
_MAX_CACHE_SIZE = 50_000

_subscribed_cache: dict[int, float] = {}
_not_subscribed_cache: dict[int, float] = {}


def _is_member_status(member: Any) -> bool:
    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return True
    # Ограниченный участник всё ещё состоит в канале
    return status == "restricted" and bool(getattr(member, "is_member", False))


def _remember(cache: dict[int, float], user_id: int, now: float) -> None:
    if len(cache) > _MAX_CACHE_SIZE:
        cache.clear()
    cache[user_id] = now


def _cached_result(user_id: int, now: float) -> bool | None:
    positive_at = _subscribed_cache.get(user_id)
    if positive_at is not None and now - positive_at < _POSITIVE_CACHE_TTL_SECONDS:
        return True
    negative_at = _not_subscribed_cache.get(user_id)
    if negative_at is not None and now - negative_at < _NEGATIVE_CACHE_TTL_SECONDS:
        return False
    return None


async def _check_channels(user_id: int) -> bool:
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
            return False
    return True


async def is_user_subscribed(user_id: int, use_cache: bool = True) -> bool:
    now = time.monotonic()
    if use_cache:
        cached = _cached_result(user_id, now)
        if cached is not None:
            return cached

    subscribed = await _check_channels(user_id)
    if subscribed:
        _not_subscribed_cache.pop(user_id, None)
        _remember(_subscribed_cache, user_id, now)
    else:
        _subscribed_cache.pop(user_id, None)
        _remember(_not_subscribed_cache, user_id, now)
    return subscribed
