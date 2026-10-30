"""
Оплата за голос (буст) канала по ссылке «Проголосовать» (t.me/boost/...).

Голос в опросе внутри бота не оплачивается. Платим за буст: Telegram присылает
событие chat_boost (кто добавил буст) и removed_chat_boost (буст убран).
Бот должен быть администратором канала, иначе Telegram эти события не присылает.

Правила:
- один раз на человека за всё время;
- только тем, кто запускал бота, не заблокирован и подписан на обязательные каналы;
- если буст убран в течение boost_hold_hours после оплаты — начисление снимается
  (0 — снимается при любой отмене);
- не снимается, если у человека остались другие бусты канала или буст истёк сам
  (закончился Premium).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiogram import Bot
from loguru import logger

from data.config import ADMINS
from data.settings import get_settings
from loader import db
from services.settings_store import set_setting
from services.subscription import is_user_subscribed_strict
from utils.boost_link import boost_target, is_boost_chat, is_natural_expiry, to_ts

Outcome = Literal["rewarded", "already", "disabled", "not_user", "blocked", "not_subscribed"]


@dataclass(frozen=True)
class BoostReward:
    outcome: Outcome
    amount: int = 0


def configured_target() -> str | int | None:
    """Канал из ссылки «Проголосовать» или None, если это не ссылка на буст."""
    return boost_target(get_settings().vote_url)


def is_tracked_chat(chat_id: int, username: str | None) -> bool:
    return is_boost_chat(configured_target(), chat_id, username, get_settings().boost_chat_id)


async def remember_chat_id(chat_id: int) -> None:
    """
    Запоминаем ID канала, чтобы узнавать его и после смены юзернейма.
    Обычно ID сохраняется вместе со ссылкой в админке; здесь — для ссылок,
    сохранённых до этого исправления.
    """
    if get_settings().boost_chat_id is None:
        await set_setting("boost_chat_id", chat_id, None)


async def reward_boost(
    user_id: int, chat_id: int, boost_id: str, expires_ts: int | None = None
) -> BoostReward:
    reward = get_settings().boost_credit
    if reward <= 0:
        # Ничего не записываем: если админ включит награду позже, человек её получит
        return BoostReward("disabled")
    if not await db.select_user(telegram_id=user_id):
        return BoostReward("not_user")
    if await db.is_user_blocked(user_id):
        return BoostReward("blocked")
    if user_id not in ADMINS and not await is_user_subscribed_strict(user_id):
        return BoostReward("not_subscribed")
    row = await db.reward_boost_once(user_id, chat_id, boost_id, reward, expires_ts)
    if row is None:
        return BoostReward("already")
    return BoostReward("rewarded", int(row["reward_sum"] or 0))


async def handle_removed_boost(
    bot: Bot, chat_id: int, boost_id: str, removed_ts: int | None
) -> tuple[int, int] | None:
    """Буст убран. Возвращает (кому снято, сколько) или None, если снимать не нужно."""
    row = await db.get_active_boost_reward(chat_id, boost_id)
    if not row:
        # Этот буст не оплачивался или уже снят
        return None
    user_id = int(row["user_id"])

    if is_natural_expiry(removed_ts, row["expires_ts"]):
        logger.info(f"🚀 Буст user={user_id} истёк сам (закончился Premium) — начисление остаётся")
        return None

    try:
        remaining = [
            b for b in (await bot.get_user_chat_boosts(chat_id=chat_id, user_id=user_id)).boosts
            if b.boost_id != boost_id
        ]
    except Exception as e:
        # Не смогли проверить — поступаем как раньше: снимаем по правилам срока
        logger.warning(f"Не удалось проверить другие бусты user={user_id}: {e}")
        remaining = []

    if remaining:
        other = remaining[0]
        await db.move_boost_reward(chat_id, boost_id, other.boost_id, to_ts(other.expiration_date))
        logger.info(f"🚀 user={user_id} убрал один буст, другие остались — начисление остаётся")
        return None

    reversed_row = await db.reverse_boost_reward(chat_id, boost_id, get_settings().boost_hold_hours)
    if not reversed_row:
        return None
    return int(reversed_row["user_id"]), int(reversed_row["reward_sum"] or 0)


def hold_note() -> str:
    hours = get_settings().boost_hold_hours
    if hours > 0:
        return f"⚠️ Если отменить голос в течение {hours} ч., начисление будет снято."
    return "⚠️ Если отменить голос, начисление будет снято."
