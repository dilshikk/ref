# services/subscription.py
"""Проверка обязательной подписки на каналы из БД."""

import asyncio
import time
from typing import Any, cast

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from loguru import logger

from loader import db, bot
from utils.subscription_status import Outcome, SubscriptionStatus, combine

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


def _cached_result(user_id: int, now: float) -> SubscriptionStatus | None:
    positive_at = _subscribed_cache.get(user_id)
    if positive_at is not None and now - positive_at < _POSITIVE_CACHE_TTL_SECONDS:
        return SubscriptionStatus.SUBSCRIBED
    negative_at = _not_subscribed_cache.get(user_id)
    if negative_at is not None and now - negative_at < _NEGATIVE_CACHE_TTL_SECONDS:
        return SubscriptionStatus.NOT_SUBSCRIBED
    return None


def _outcome(channel: dict[str, Any], result: object) -> Outcome:
    if not isinstance(result, BaseException):
        return "member" if _is_member_status(result) else "not_member"
    if isinstance(result, (TelegramBadRequest, TelegramForbiddenError)):
        # Канал настроен неверно (бот не админ / канал удалён)
        logger.warning(f"Не удалось проверить подписку на {channel['chat_id']}: {result}")
        return "config"
    # Сеть, таймаут, 5xx Telegram: результат неизвестен
    logger.error(f"Ошибка проверки подписки {channel['chat_id']}: {result!r}")
    return "error"


async def _check_channels(user_id: int, *, strict: bool = False) -> SubscriptionStatus:
    """
    Каналы проверяются параллельно (asyncio.gather). Сбой проверки больше не
    засчитывается как подписка (#24): итог UNKNOWN. strict=True (начисление денег):
    неверно настроенный канал тоже означает «не подписан».
    """
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    if not channels:
        return SubscriptionStatus.SUBSCRIBED
    results = await asyncio.gather(
        *(bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id) for ch in channels),
        return_exceptions=True,
    )
    return combine((_outcome(ch, r) for ch, r in zip(channels, results)), strict=strict)


async def get_subscription_status(user_id: int, use_cache: bool = True) -> SubscriptionStatus:
    now = time.monotonic()
    if use_cache:
        cached = _cached_result(user_id, now)
        if cached is not None:
            return cached

    try:
        status = await _check_channels(user_id)
    except Exception as e:
        # Например, недоступна БД со списком каналов
        logger.error(f"Проверка подписки не удалась user={user_id}: {e!r}")
        return SubscriptionStatus.UNKNOWN

    if status is SubscriptionStatus.SUBSCRIBED:
        _not_subscribed_cache.pop(user_id, None)
        _remember(_subscribed_cache, user_id, now)
    elif status is SubscriptionStatus.NOT_SUBSCRIBED:
        _subscribed_cache.pop(user_id, None)
        _remember(_not_subscribed_cache, user_id, now)
    # UNKNOWN не кэшируем: при следующем сообщении проверим снова
    return status


async def is_user_subscribed(user_id: int, use_cache: bool = True) -> bool:
    """True только при подтверждённой подписке; сбой проверки = False."""
    return await get_subscription_status(user_id, use_cache) is SubscriptionStatus.SUBSCRIBED


async def is_user_subscribed_strict(user_id: int) -> bool:
    """Для начислений: без кэша, любая ошибка проверки = не подписан."""
    try:
        status = await _check_channels(user_id, strict=True)
    except Exception as e:
        logger.warning(f"Строгая проверка подписки не удалась user={user_id}: {e!r}")
        return False
    return status is SubscriptionStatus.SUBSCRIBED
