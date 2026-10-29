# handlers/private/admin/stats.py
"""📊 Статистика: общая сводка, новые пользователи, активность, периоды."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.types import ReplyKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import stats_kb
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from services.activity import activity_service

from .common import (
    get_channel_totals,
    get_finance_totals,
    get_referral_totals,
    get_user_totals,
    show_placeholder,
)

router = private_only(Router(name="private.admin.stats"))


async def _show_main_section(message: types.Message, text: str, keyboard: ReplyKeyboardMarkup) -> None:
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(F.text == "📊 Статистика", IsBotAdminFilter(ADMINS))
async def stats_menu(message: types.Message):
    users = await get_user_totals()
    channels = await get_channel_totals()
    referrals = await get_referral_totals()
    payouts = await get_finance_totals()
    activity = await activity_service.dashboard()
    await _show_main_section(
        message,
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users['total_users']}</b>\n"
        f"🚫 Заблокированных: <b>{users['blocked_users']}</b>\n"
        f"📢 Каналов: <b>{channels['total_channels']}</b>\n"
        f"🎁 Всего рефералов: <b>{referrals['total_referrals']}</b>\n"
        f"💸 Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n\n"
        f"Сегодня DAU: <b>{activity['dau']}</b>\n"
        f"WAU: <b>{activity['wau']}</b>\n"
        f"MAU: <b>{activity['mau']}</b>\n"
        f"Retention: <b>{activity['retention']}%</b>\n"
        f"Новые пользователи сегодня: <b>{activity['new_today']}</b>\n"
        f"Вернувшиеся сегодня: <b>{activity['returning_today']}</b>",
        stats_kb(),
    )


@router.message(F.text == "👥 Новые пользователи", IsBotAdminFilter(ADMINS))
async def stats_new_users(message: types.Message):
    users = await get_user_totals()
    new_1d = await db.count_new_users_since(1)
    new_7d = await db.count_new_users_since(7)
    new_30d = await db.count_new_users_since(30)

    yesterday_row = cast(dict[str, Any] | None, await db.execute(
        """
        SELECT COUNT(*) AS count
        FROM Users
        WHERE created_at >= NOW() - INTERVAL '48 hours'
          AND created_at < NOW() - INTERVAL '24 hours'
        """,
        fetchone=True,
    ))
    yesterday = int(yesterday_row["count"] or 0) if yesterday_row else 0
    trend = new_1d - yesterday
    if trend > 0:
        trend_text = f"📈 +{trend} к вчера"
    elif trend < 0:
        trend_text = f"📉 {trend} к вчера"
    else:
        trend_text = "➖ Без изменений к вчера"

    await message.answer(
        "👥 <b>Новые пользователи</b>\n\n"
        f"Всего зарегистрировано: <b>{users['total_users']}</b>\n"
        f"За 24ч: <b>{new_1d}</b>\n"
        f"За 7д: <b>{new_7d}</b>\n"
        f"За 30д: <b>{new_30d}</b>\n\n"
        f"{trend_text}",
        parse_mode="HTML",
        reply_markup=stats_kb(),
    )


@router.message(F.text == "📈 Активность", IsBotAdminFilter(ADMINS))
async def stats_activity(message: types.Message):
    activity = await activity_service.dashboard()
    events_1d = await db.count_activity_events_since(1)
    events_7d = await db.count_activity_events_since(7)
    await message.answer(
        "📈 <b>Активность</b>\n\n"
        f"DAU (24ч): <b>{activity['dau']}</b>\n"
        f"WAU (7д): <b>{activity['wau']}</b>\n"
        f"MAU (30д): <b>{activity['mau']}</b>\n"
        f"Retention: <b>{activity['retention']}%</b>\n"
        f"Новые за 24ч: <b>{activity['new_today']}</b>\n"
        f"Вернувшиеся за 24ч: <b>{activity['returning_today']}</b>\n\n"
        f"События за 24ч: <b>{events_1d}</b>\n"
        f"События за 7д: <b>{events_7d}</b>",
        parse_mode="HTML",
        reply_markup=stats_kb(),
    )


@router.message(F.text == "📢 Каналы", IsBotAdminFilter(ADMINS))
async def stats_channels(message: types.Message):
    channels = await get_channel_totals()
    await show_placeholder(
        message,
        "📢 Каналы",
        f"Всего подключено: <b>{channels['total_channels']}</b>.",
        stats_kb(),
    )


@router.message(F.text == "💳 Финансы", IsBotAdminFilter(ADMINS))
async def stats_finance(message: types.Message):
    payouts = await get_finance_totals()
    await show_placeholder(
        message,
        "💳 Финансы",
        f"Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n"
        f"Балансовых единиц в системе: <b>{payouts['balance_units']}</b>.",
        stats_kb(),
    )


@router.message(F.text == "🎁 Реферальная статистика", IsBotAdminFilter(ADMINS))
async def stats_referrals(message: types.Message):
    referrals = await get_referral_totals()
    await show_placeholder(
        message,
        "🎁 Реферальная статистика",
        f"Всего приглашений: <b>{referrals['total_referrals']}</b>\n"
        f"Текущий активный счётчик: <b>{referrals['current_referrals']}</b>.",
        stats_kb(),
    )


@router.message(F.text.in_({"📅 Сегодня", "📆 Неделя", "🗓 Месяц", "🌍 За всё время"}), IsBotAdminFilter(ADMINS))
async def stats_timeframes(message: types.Message):
    period_map: dict[str, int | None] = {
        "📅 Сегодня": 1,
        "📆 Неделя": 7,
        "🗓 Месяц": 30,
        "🌍 За всё время": None,
    }
    title = message.text or "Статистика"
    days = period_map.get(title)

    if days is None:
        users_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM Users",
            fetchone=True,
        ))
        active_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(DISTINCT user_id) AS count FROM user_activity",
            fetchone=True,
        ))
        activity_events_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM user_activity",
            fetchone=True,
        ))
        audit_events_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM audit_logs",
            fetchone=True,
        ))

        total_users = int(users_row["count"] or 0) if users_row else 0
        active_users = int(active_row["count"] or 0) if active_row else 0
        activity_events = int(activity_events_row["count"] or 0) if activity_events_row else 0
        audit_events = int(audit_events_row["count"] or 0) if audit_events_row else 0

        text = (
            f"<b>{title}</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"🟢 Активные пользователи: <b>{active_users}</b>\n"
            f"📌 События активности: <b>{activity_events}</b>\n"
            f"🧾 Аудит-события: <b>{audit_events}</b>"
        )
    else:
        active_users = await db.count_active_users_since(days)
        new_users = await db.count_new_users_since(days)
        returning_users = await db.count_returning_users_since(days)
        activity_events = await db.count_activity_events_since(days)
        audit_events = await db.count_audit_logs_since(days)

        text = (
            f"<b>{title}</b>\n\n"
            f"🟢 Активные пользователи: <b>{active_users}</b>\n"
            f"🆕 Новые пользователи: <b>{new_users}</b>\n"
            f"🔁 Вернувшиеся пользователи: <b>{returning_users}</b>\n"
            f"📌 События активности: <b>{activity_events}</b>\n"
            f"🧾 Аудит-события: <b>{audit_events}</b>"
        )

    await message.answer(text, parse_mode="HTML", reply_markup=stats_kb())
