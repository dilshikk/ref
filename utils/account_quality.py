# utils/account_quality.py
"""
Оценка «живости» участника, добавленного в группу (#18).

Раньше норма «добавил N участников» выполнялась теми же фейковыми аккаунтами,
что и накрутка. Bot API не сообщает возраст аккаунта, поэтому опираемся на то,
что видно боту: сколько человек пробыл в группе, писал ли туда, запускал ли бота,
заполнен ли профиль. Аккаунты для накрутки обычно ничего из этого не делают.

Модуль без зависимостей от aiogram и БД, чтобы его было просто тестировать.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Verdict = Literal["qualified", "wait", "rejected"]

WEIGHT_WROTE_IN_GROUP = 2
WEIGHT_STARTED_BOT = 2
WEIGHT_PREMIUM = 2
WEIGHT_PHOTO = 1
WEIGHT_USERNAME = 1


@dataclass(frozen=True)
class MemberSignals:
    hours_in_group: float
    still_member: bool
    wrote_in_group: bool
    started_bot: bool
    has_photo: bool
    has_username: bool
    is_premium: bool


def is_enabled(min_hours: int, min_score: int) -> bool:
    """add_min_hours = 0 и add_min_score = 0 — проверка выключена, счётчик растёт сразу (как раньше)."""
    return min_hours > 0 or min_score > 0


def score(s: MemberSignals) -> int:
    return (
        WEIGHT_WROTE_IN_GROUP * int(s.wrote_in_group)
        + WEIGHT_STARTED_BOT * int(s.started_bot)
        + WEIGHT_PREMIUM * int(s.is_premium)
        + WEIGHT_PHOTO * int(s.has_photo)
        + WEIGHT_USERNAME * int(s.has_username)
    )


def evaluate(s: MemberSignals, min_hours: int, min_score: int) -> Verdict:
    """
    rejected  — уже не в группе (засчитывать некого);
    wait      — пробыл меньше min_hours или пока мало активности;
    qualified — можно засчитать пригласившему.
    """
    if not s.still_member:
        return "rejected"
    if s.hours_in_group < min_hours:
        return "wait"
    return "qualified" if score(s) >= min_score else "wait"


def is_member_status(member: object) -> bool:
    """Состоит ли пользователь в чате по ответу get_chat_member."""
    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return True
    # Ограниченный участник всё ещё состоит в чате
    return status == "restricted" and bool(getattr(member, "is_member", False))
