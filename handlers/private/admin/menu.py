# handlers/private/admin/menu.py
"""Вход в админ-панель (/admin), «Назад» и общая отмена inline-кнопкой «Нет»."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from loguru import logger
from filters import private_only
from keyboards.reply_keyboards import menu_kb, reply_keyboard_for_admin
from filters.admin import IsBotAdminFilter
from data.config import ADMINS

router = private_only(Router(name="private.admin.menu"))


@router.message(F.text == "🔙 Назад", IsBotAdminFilter(ADMINS))
async def go_back_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


@router.message(F.text == "⬅️ Назад", IsBotAdminFilter(ADMINS))
async def go_back_admin_alt(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


@router.message(F.text == "👤 Меню пользователя", IsBotAdminFilter(ADMINS))
async def open_user_menu(message: types.Message, state: FSMContext):
    # #97: вместо «Назад», который никуда не вёл. Вернуться в админку — /admin
    await state.clear()
    await message.answer(
        "👤 Меню пользователя. Вернуться в админ-панель — /admin",
        reply_markup=menu_kb(),
    )


@router.message(Command("admin"), IsBotAdminFilter(ADMINS))
async def admin_panel(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в <b>админ-панель</b>!",
        parse_mode="HTML",
        reply_markup=reply_keyboard_for_admin(),
    )


@router.callback_query(F.data == "no", IsBotAdminFilter(ADMINS))
async def cancel_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    msg_any = cast(Any, msg)
    try:
        await msg_any.delete()
    except TelegramAPIError as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    await msg_any.answer("❌ Действие отменено.", reply_markup=reply_keyboard_for_admin())
    await state.clear()
