# services/group_adds.py
"""
Проверка добавленных в группу перед зачётом в норму «добавил N участников» (#18).

Проверка запускается по кнопке «✅ Я добавил(а)» и при выводе средств, без фоновых
задач: для каждого непроверенного добавленного собираем сигналы (БД + Telegram)
и решаем — засчитать, подождать или отклонить.

Деньги «на проверке» (#46): начисление за приглашённого доступно к выводу, только
когда его уже нельзя снять (прошло referral_hold_hours) и приглашённый прошёл ту же
проверку живости. Отклонённый проверкой приглашённый снимает своё начисление.

Скорость (#46): одна проверка — до 50 запросов к Telegram, а вызывалась она и на
кнопке вывода, и на подтверждении заявки. Теперь результат для пользователя
переиспользуется _QUALIFY_COOLDOWN_SECONDS секунд.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from data.settings import get_settings
from loader import bot, db
from utils.account_quality import MemberSignals, evaluate, is_enabled, is_member_status

_BATCH_LIMIT = 50
# Не больше стольких запросов к Telegram одновременно
_telegram_slots = asyncio.Semaphore(5)
# Ответы Telegram, после которых участника точно нет
_GONE_MARKERS = ("user not found", "participant_id_invalid", "user_id_invalid")
# Повторная проверка одного пользователя не чаще, чем раз в столько секунд
_QUALIFY_COOLDOWN_SECONDS = 120
# Сколько последних результатов держать в памяти
_QUALIFY_CACHE_MAX = 5000


@dataclass(frozen=True)
class QualifyReport:
    qualified: int = 0
    rejected: int = 0
    waiting: int = 0


# (group_id, inviter_id) → (время проверки, результат)
_last_qualify: dict[tuple[int, int], tuple[float, QualifyReport]] = {}


def qualification_enabled() -> bool:
    s = get_settings()
    return is_enabled(s.add_min_hours, s.add_min_score)


async def unverified_referral_sum(user_id: int) -> int:
    """Сколько сум из баланса пока «на проверке» и не выводится (#46)."""
    settings = get_settings()
    # Группа из bot_settings (#116). Пока она не задана, вывод всё равно
    # заблокирован (проверка нормы и одобрение требуют группу), поэтому 0 безопасен.
    return await db.get_unverified_referral_sum(
        user_id,
        hold_hours=settings.referral_hold_hours,
        group_id=settings.group_id or 0,
        check_quality=qualification_enabled(),
    )


def locked_explanation() -> str:
    """Когда деньги «на проверке» станут доступны — для сообщений пользователю."""
    settings = get_settings()
    parts: list[str] = []
    if settings.referral_hold_hours > 0:
        parts.append(f"через {settings.referral_hold_hours} ч. после вступления друга")
    if qualification_enabled():
        parts.append("когда друг проявит активность (напишет в группу или запустит бота)")
    if not parts:
        return ""
    return "Деньги за приглашённого станут доступны к выводу " + " и ".join(parts) + "."


async def _has_photo(member_id: int) -> bool:
    try:
        photos = await bot.get_user_profile_photos(member_id, limit=1)
    except Exception as e:
        logger.debug(f"Фото профиля {member_id} не получено: {e}")
        return False
    return bool(photos.total_count)


async def _collect(row: Any, group_id: int) -> MemberSignals | None:
    """Сигналы участника. None — Telegram не ответил, решение откладываем (а не засчитываем)."""
    member_id = int(row["member_id"])
    async with _telegram_slots:
        try:
            member = await bot.get_chat_member(chat_id=group_id, user_id=member_id)
        except TelegramBadRequest as e:
            if any(m in str(e).lower() for m in _GONE_MARKERS):
                # Аккаунт удалён или не состоит в группе — засчитывать некого
                logger.info(f"Добавленный {member_id} не найден в группе: {e}")
                return MemberSignals(0, False, False, False, False, False, False)
            # Например, бот потерял права в группе: решение откладываем, а не отклоняем всех
            logger.warning(f"Не удалось проверить добавленного {member_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Не удалось проверить добавленного {member_id}: {e}")
            return None
        has_photo = await _has_photo(member_id)
    user = getattr(member, "user", None)
    return MemberSignals(
        hours_in_group=float(row["hours_in_group"] or 0),
        still_member=is_member_status(member),
        wrote_in_group=bool(row["wrote_in_group"]),
        started_bot=bool(row["started_bot"]),
        has_photo=has_photo,
        has_username=bool(getattr(user, "username", None)),
        is_premium=bool(getattr(user, "is_premium", False)),
    )


def _remember(key: tuple[int, int], now: float, report: QualifyReport) -> None:
    _last_qualify[key] = (now, report)
    if len(_last_qualify) > _QUALIFY_CACHE_MAX:
        for k, (at, _) in list(_last_qualify.items()):
            if now - at >= _QUALIFY_COOLDOWN_SECONDS:
                _last_qualify.pop(k, None)


async def qualify_pending(group_id: int, inviter_id: int) -> QualifyReport:
    if not qualification_enabled():
        return QualifyReport()
    key = (group_id, inviter_id)
    now = time.monotonic()
    cached = _last_qualify.get(key)
    if cached and now - cached[0] < _QUALIFY_COOLDOWN_SECONDS:
        return cached[1]
    report = await _qualify_now(group_id, inviter_id)
    _remember(key, now, report)
    return report


async def _qualify_now(group_id: int, inviter_id: int) -> QualifyReport:
    settings = get_settings()
    rows = list(await db.list_pending_adds(group_id, inviter_id, limit=_BATCH_LIMIT) or [])
    if not rows:
        return QualifyReport()

    signals = await asyncio.gather(*(_collect(row, group_id) for row in rows))
    qualified = rejected = 0
    for row, s in zip(rows, signals):
        if s is None:
            continue
        verdict = evaluate(s, settings.add_min_hours, settings.add_min_score)
        try:
            if verdict == "qualified" and await db.qualify_pending_add(int(row["id"])):
                qualified += 1
            elif verdict == "rejected":
                # Отклонённый по реферальной ссылке: начисление за него снимается (#46)
                reversed_row = await db.reject_pending_add(int(row["id"]))
                rejected += 1
                if reversed_row:
                    logger.info(
                        f"↩️ Начисление {reversed_row['reward_sum']} сум снято: "
                        f"{row['member_id']} не прошёл проверку (inviter={inviter_id})"
                    )
        except Exception as e:
            logger.error(f"Ошибка проверки добавленного {row['member_id']} (inviter={inviter_id}): {e}")

    waiting = await db.count_pending_adds(group_id, inviter_id)
    if qualified or rejected:
        logger.info(
            f"👥 Проверка добавленных inviter={inviter_id}: засчитано {qualified}, "
            f"отклонено {rejected}, ждут {waiting}"
        )
    return QualifyReport(qualified=qualified, rejected=rejected, waiting=waiting)
