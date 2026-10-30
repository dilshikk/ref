# handlers/private/admin/security.py
"""🔒 Безопасность: баны и антифрод-лимит.
Заглушки «🚫 Бан групп», «📋 Белый список» и дубль лимитов «📱 Ограничения» удалены (#83, #92, #94)."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import (
    reply_keyboard_for_admin,
    security_kb,
)
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings
from services.settings_store import set_setting

from .common import blocked_users_text, cancel_kb, forms_router, record_setting_change

router = private_only(Router(name="private.admin.security"))

# Показываем значения через get_settings() — те же, по которым работает бот (#49).
# Сохраняем через set_setting() в таблицу bot_settings (#115).


@router.message(F.text == "🔒 Безопасность", IsBotAdminFilter(ADMINS))
async def security_menu(message: types.Message):
    await message.answer(
        "🔒 <b>Безопасность</b>\n\n"
        "Антифрод и защитные лимиты.",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "🚫 Бан пользователей", IsBotAdminFilter(ADMINS))
async def security_ban_users(message: types.Message):
    # #78: тот же список, что в «👥 Пользователи», плюс подсказка, как заблокировать
    await message.answer(
        await blocked_users_text()
        + "\n\nЧтобы заблокировать, откройте пользователя через «🔍 Поиск пользователя» "
        "в разделе «👥 Пользователи».",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "🛡 Антиспам", IsBotAdminFilter(ADMINS))
async def security_antispam(message: types.Message):
    await edit_antispam_limit(message)


@router.message(F.text == "🚨 Антифрод лимит", IsBotAdminFilter(ADMINS))
async def edit_antispam_limit(message: types.Message):
    value = get_settings().antispam_referral_limit
    await message.answer(
        f"🚨 Текущий лимит рефералов в час: <b>{value}</b>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("antispam_limit"),
    )


@router.callback_query(F.data == "yes_antispam_limit", IsBotAdminFilter(ADMINS))
async def confirm_antispam_limit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый лимит рефералов в час.\nСейчас: {get_settings().antispam_referral_limit}\n"
        "Пример: <code>50</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_antispam_limit)


@forms_router.message(AdminFlow.set_antispam_limit, IsBotAdminFilter(ADMINS))
async def update_antispam_limit(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>50</code>", parse_mode="HTML")
        return
    new_value = int(text)
    old_value = await set_setting("antispam_referral_limit", new_value, user.id)
    await record_setting_change(user.id, "antispam_referral_limit", old_value, new_value)
    await message.answer(
        f"✅ Лимит антифрода изменён на <b>{new_value}</b> рефералов/час.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()
