# handlers/private/admin/stats.py
"""📊 Статистика: только просмотр — общая сводка, новые пользователи, активность, периоды.
Заглушки «📢 Каналы» и «💳 Финансы» заменены настоящими данными (#73, #84).
Топ пригласивших перенесён сюда из «👥 Пользователи» → «🎁 Рефералы» (был дублем)."""
import html
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import stats_kb
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings
from services.activity import activity_service

from .common import (
    get_channel_totals,
    get_finance_totals,
    get_referral_totals,
    get_user_totals,
)

router = private_only(Router(name="private.admin.stats"))

TOP_REFERRERS_LIMIT = 10


async def _show_main_section(message: types.Message, text: str, keyboard: ReplyKeyboardMarkup) -> None:
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _count(sql: str) -> int:
    row = cast(dict[str, Any] | None, await db.execute(sql, fetchone=True))
    return int((row or {}).get("count") or 0)


# Кнопка «📊 Статистика» и команда /stats из меню админа (utils/bot_commands.py)
@router.message(F.text == "📊 Статистика", IsBotAdminFilter(ADMINS))
@router.message(Command("stats"), IsBotAdminFilter(ADMINS))
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

    yesterday = await _count(
        """
        SELECT COUNT(*) AS count
        FROM Users
        WHERE created_at >= NOW() - INTERVAL '48 hours'
          AND created_at < NOW() - INTERVAL '24 hours'
        """
    )
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
    # #73: список обязательных каналов вместо одного числа
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    settings = get_settings()
    if not channels:
        body = (
            "Обязательные каналы не добавлены.\n"
            "Добавить: «📢 Каналы и группы» → «📌 Обязательные подписки»."
        )
    else:
        lines = [
            f"{i}. <code>{html.escape(str(ch['chat_id']))}</code> — {html.escape(str(ch['link']))}"
            for i, ch in enumerate(channels, start=1)
        ]
        body = f"Обязательных каналов: <b>{len(channels)}</b>\n\n" + "\n".join(lines)
    await message.answer(
        "📢 <b>Каналы</b>\n\n"
        f"{body}\n\n"
        f"👤 Нужно добавить в группу для вывода: <b>{settings.group_member_count}</b>",
        parse_mode="HTML",
        reply_markup=stats_kb(),
        disable_web_page_preview=True,
    )


@router.message(F.text == "💳 Финансы", IsBotAdminFilter(ADMINS))
async def stats_finance(message: types.Message):
    # #84: суммы в деньгах, а не количество приглашённых
    f = await get_finance_totals()
    debt_line = f"⚠️ Долги пользователей: <b>{f['debt_sum']}</b> сум\n" if f["debt_sum"] > 0 else ""
    await message.answer(
        "💳 <b>Финансы</b>\n\n"
        f"💸 Ожидают выплаты: <b>{f['pending_payouts']}</b> заявок на <b>{f['pending_sum']}</b> сум\n"
        f"✅ Выплачено всего: <b>{f['approved_payouts']}</b> заявок на <b>{f['approved_sum']}</b> сум\n"
        f"📅 Выплачено по заявкам за 30 дней: <b>{f['approved_30d_sum']}</b> сум\n\n"
        f"💵 На балансах пользователей: <b>{f['balance_sum']}</b> сум\n"
        f"{debt_line}"
        f"📊 Начислено за всё время: <b>{f['earned_sum']}</b> сум",
        parse_mode="HTML",
        reply_markup=stats_kb(),
    )


@router.message(F.text == "🎁 Реферальная статистика", IsBotAdminFilter(ADMINS))
async def stats_referrals(message: types.Message):
    referrals = await get_referral_totals()
    on_hold = await _count("SELECT COUNT(*) AS count FROM referral_holds WHERE status = 'pending'")
    reversed_count = await _count("SELECT COUNT(*) AS count FROM UserInviteMember WHERE reversed_at IS NOT NULL")
    top = cast(list[dict[str, Any]], await db.get_top_referrals(limit=TOP_REFERRERS_LIMIT) or [])
    top_lines = [
        f"{index}. <b>{html.escape(row['full_name'] or '—')}</b> — <b>{row['history_count']}</b>"
        for index, row in enumerate(top, start=1)
    ]
    top_text = (
        f"\n\n🏆 <b>Топ-{TOP_REFERRERS_LIMIT} пригласивших</b>\n" + "\n".join(top_lines)
        if top_lines else "\n\nРефералов пока нет."
    )
    await message.answer(
        "🎁 <b>Реферальная статистика</b>\n\n"
        f"Всего приглашений: <b>{referrals['total_referrals']}</b>\n"
        f"Текущий активный счётчик: <b>{referrals['current_referrals']}</b>\n"
        f"⏸ На удержании (ждут решения): <b>{on_hold}</b>\n"
        f"↩️ Снято за выход из группы: <b>{reversed_count}</b>"
        f"{top_text}",
        parse_mode="HTML",
        reply_markup=stats_kb(),
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
        total_users = await _count("SELECT COUNT(*) AS count FROM Users")
        active_users = await _count("SELECT COUNT(DISTINCT user_id) AS count FROM user_activity")
        activity_events = await _count("SELECT COUNT(*) AS count FROM user_activity")
        audit_events = await _count("SELECT COUNT(*) AS count FROM audit_logs")

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
