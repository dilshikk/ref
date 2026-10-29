# handlers/private/admin/users.py
"""👥 Пользователи: списки, заблокированные, администраторы, рефералы.
Поиск и изменение баланса — в handlers/private/user_management.py."""
import html
from typing import Any, cast
from aiogram import Router, types, F
from filters import private_only
from keyboards.reply_keyboards import users_kb
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS

router = private_only(Router(name="private.admin.users"))


@router.message(F.text == "👥 Пользователи", IsBotAdminFilter(ADMINS))
async def users_menu(message: types.Message):
    await message.answer(
        "👥 <b>Пользователи</b>\n\n"
        "Откройте список или найдите конкретного пользователя.",
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "📋 Все пользователи", IsBotAdminFilter(ADMINS))
async def users_list_alias(message: types.Message) -> None:
    from ..user_management import users_list_start

    await users_list_start(message)


@router.message(F.text == "🚫 Заблокированные пользователи", IsBotAdminFilter(ADMINS))
async def blocked_users_menu(message: types.Message):
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    blocked = [user for user in users if user.get("is_blocked")]
    text = (
        f"🚫 <b>Заблокированные пользователи</b>\n\nВсего: <b>{len(blocked)}</b>"
        if blocked
        else "🚫 <b>Заблокированные пользователи</b>\n\nСейчас заблокированных пользователей нет."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=users_kb())


@router.message(F.text == "👑 Администраторы", IsBotAdminFilter(ADMINS))
async def admins_menu(message: types.Message):
    admin_lines = "\n".join(f"• <code>{admin}</code>" for admin in ADMINS)
    await message.answer(
        "👑 <b>Администраторы</b>\n\n" + admin_lines,
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "🎁 Рефералы", IsBotAdminFilter(ADMINS))
async def referrals_menu(message: types.Message):
    top_referrals = cast(list[dict[str, Any]], await db.get_top_referrals(limit=10) or [])
    if not top_referrals:
        await message.answer("🎁 Рефералов пока нет.", reply_markup=users_kb())
        return
    lines = [
        f"{index}. <b>{html.escape(row['full_name'] or '—')}</b> — <b>{row['history_count']}</b>"
        for index, row in enumerate(top_referrals, start=1)
    ]
    await message.answer(
        "🎁 <b>Рефералы</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=users_kb(),
    )
