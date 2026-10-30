"""
Оплата за голос (буст) канала по ссылке «Проголосовать» (t.me/boost/...).

Голос в опросе внутри бота не оплачивается. Платим за буст: Telegram присылает
событие chat_boost (кто добавил буст) и removed_chat_boost (буст убран).
Бот должен быть администратором канала, иначе Telegram эти события не присылает.

Правила:
- один раз на человека за всё время;
- только тем, кто запускал бота, не заблокирован и подписан на обязательные каналы;
- если буст убран в течение boost_hold_hours после оплаты — начисление снимается
  (0 — снимается при любой отмене).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from data.config import ADMINS
from data.settings import get_settings
from loader import db
from services.subscription import is_user_subscribed_strict
from utils.boost_link import boost_target, is_boost_chat

Outcome = Literal["rewarded", "already", "disabled", "not_user", "blocked", "not_subscribed"]


@dataclass(frozen=True)
class BoostReward:
    outcome: Outcome
    amount: int = 0


def configured_target() -> str | int | None:
    """Канал из ссылки «Проголосовать» или None, если это не ссылка на буст."""
    return boost_target(get_settings().vote_url)


def is_tracked_chat(chat_id: int, username: str | None) -> bool:
    return is_boost_chat(configured_target(), chat_id, username)


async def reward_boost(user_id: int, chat_id: int, boost_id: str) -> BoostReward:
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
    row = await db.reward_boost_once(user_id, chat_id, boost_id, reward)
    if row is None:
        return BoostReward("already")
    return BoostReward("rewarded", int(row["reward_sum"] or 0))


async def reverse_boost(chat_id: int, boost_id: str) -> tuple[int, int] | None:
    """(кому снято, сколько) или None."""
    row = await db.reverse_boost_reward(chat_id, boost_id, get_settings().boost_hold_hours)
    if not row:
        return None
    return int(row["user_id"]), int(row["reward_sum"] or 0)


def hold_note() -> str:
    hours = get_settings().boost_hold_hours
    if hours > 0:
        return f"⚠️ Если отменить голос в течение {hours} ч., начисление будет снято."
    return "⚠️ Если отменить голос, начисление будет снято."
