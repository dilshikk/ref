# services/group_adds.py
"""
Добавленные в группу участники и деньги «на проверке».

Проверка «живости» добавленных (пробыть в группе 24 ч. и проявить активность, #18)
УБРАНА: пользователи ждали сутки и не понимали, почему их добавленных не засчитывают.
Теперь добавленный засчитывается в норму «добавил N участников» сразу.
Защита от накрутки остаётся: один человек засчитывается один раз, а при выходе
из группы в срок referral_hold_hours начисление и счётчик снимаются.

Функции qualification_enabled / qualify_pending оставлены, чтобы не менять
вызывающий код: проверка просто всегда выключена.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from data.settings import get_settings
from loader import db


@dataclass(frozen=True)
class QualifyReport:
    qualified: int = 0
    rejected: int = 0
    waiting: int = 0


def qualification_enabled() -> bool:
    """Проверка «24 ч. + активность» убрана — добавленный засчитывается сразу."""
    return False


async def unverified_referral_sum(user_id: int) -> int:
    """Сколько сум из баланса пока «на проверке» (не прошёл срок referral_hold_hours, #46)."""
    settings = get_settings()
    return await db.get_unverified_referral_sum(
        user_id,
        hold_hours=settings.referral_hold_hours,
        group_id=settings.group_id or 0,
        check_quality=False,
    )


def locked_explanation() -> str:
    """Когда деньги «на проверке» станут доступны — для сообщений пользователю."""
    hold_hours = get_settings().referral_hold_hours
    if hold_hours <= 0:
        return ""
    return f"Деньги за приглашённого станут доступны к выводу через {hold_hours} ч. после вступления друга."


async def qualify_pending(group_id: int, inviter_id: int) -> QualifyReport:
    """Проверки больше нет: добавленные засчитываются сразу при вступлении."""
    return QualifyReport()


async def release_waiting_adds() -> None:
    """
    Миграция при старте: все, кто ещё ждал проверки «24 ч. + активность»,
    засчитываются добавившим сейчас (+1 к «добавил N участников» за каждого).
    Ушедшие из группы уже отклонены при выходе и сюда не попадают.
    Повторный запуск ничего не меняет: открытых записей больше нет.
    """
    rows = await db.execute(
        """
        WITH q AS (
            UPDATE pending_group_adds
            SET qualified_at = NOW()
            WHERE qualified_at IS NULL AND rejected_at IS NULL
            RETURNING group_id, inviter_id
        ), agg AS (
            SELECT group_id, inviter_id, COUNT(*)::int AS c
            FROM q GROUP BY group_id, inviter_id
        )
        INSERT INTO GroupMembers (group_id, user_id, add_user_count)
        SELECT group_id, inviter_id, c FROM agg
        ON CONFLICT (group_id, user_id) DO UPDATE
            SET add_user_count = GroupMembers.add_user_count + EXCLUDED.add_user_count
        RETURNING user_id
        """,
        fetchall=True,
    )
    if rows:
        logger.info(f"✅ Засчитаны добавленные, ждавшие проверки, у {len(rows)} пользователей")
