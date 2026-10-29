# handlers/private/admin/users.py
"""👥 Пользователи: списки, заблокированные, администраторы, рефералы.
Поиск, профиль и изменение баланса — в handlers/private/user_management.py."""
import html
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import users_kb
from loader import db
from filters.admin import IsBotAdminFilter, IsMoneyAdmin
from data.config import ADMINS
from states.flows import UserManagementFlow

from .common import blocked_users_text

router = private_only(Router(name="private.admin.users"))

_ASK_USER = "Введите ID, @username или имя пользователя:"


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


@router.message(F.text == "💰 Изменить баланс", IsBotAdminFilter(ADMINS))
async def users_change_balance(message: types.Message, state: FSMContext) -> None:
    # #76: денежные операции — только MONEY_ADMINS (#23); кнопки в профиле проверяют это ещё раз
    if not await IsMoneyAdmin()(message):
        await message.answer("⛔ Изменять баланс могут только денежные админы.", reply_markup=users_kb())
        return
    await state.set_state(UserManagementFlow.search_input)
    await state.update_data(search_purpose="balance")
    await message.answer(f"💰 Изменение баланса.\n{_ASK_USER}")


@router.message(F.text == "📨 Отправить сообщение", IsBotAdminFilter(ADMINS))
async def users_send_message(message: types.Message, state: FSMContext) -> None:
    # #77: после выбора пользователя сразу открывается ввод текста (user_management.py)
    await state.set_state(UserManagementFlow.search_input)
    await state.update_data(search_purpose="message")
    await message.answer(f"📨 Отправить сообщение.\n{_ASK_USER}")


@router.message(F.text == "🚫 Заблокированные пользователи", IsBotAdminFilter(ADMINS))
async def blocked_users_menu(message: types.Message):
    # #78: список с переходом в профиль, а не только число
    await message.answer(await blocked_users_text(), parse_mode="HTML", reply_markup=users_kb())


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
