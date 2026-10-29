# handlers/private/admin/security.py
"""🔒 Безопасность: баны, ограничения, антифрод-лимит."""
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
from data.config import ADMINS, url_yaml
from data.settings import get_settings
from utils.yaml_io import save_url_yaml

from .common import cancel_kb, forms_router, get_user_totals, record_setting_change, show_placeholder

router = private_only(Router(name="private.admin.security"))

# Показываем значения через get_settings() — те же, по которым работает бот (#49).
# Раньше здесь было url_yaml.get(..., 100), а бот по умолчанию считал лимит 20.
# url_yaml остаётся только для записи новых значений и для old_value в аудите.


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
    users = await get_user_totals()
    await message.answer(
        "🚫 <b>Бан пользователей</b>\n\n"
        f"Заблокировано: <b>{users['blocked_users']}</b>",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "🚫 Бан групп", IsBotAdminFilter(ADMINS))
async def security_ban_groups(message: types.Message):
    await show_placeholder(
        message,
        "🚫 Бан групп",
        "Механизм блокировки групп можно добавить отдельной таблицей blacklist.",
        security_kb(),
    )


@router.message(F.text == "🛡 Антиспам", IsBotAdminFilter(ADMINS))
async def security_antispam(message: types.Message):
    await edit_antispam_limit(message)


@router.message(F.text == "📱 Ограничения", IsBotAdminFilter(ADMINS))
async def security_limits(message: types.Message):
    settings = get_settings()
    await show_placeholder(
        message,
        "📱 Ограничения",
        f"Антифрод лимит: <b>{settings.antispam_referral_limit}</b> в час, "
        f"<b>{settings.antispam_referral_daily_limit}</b> в сутки\n"
        f"Порог участников для вывода: <b>{settings.group_member_count}</b>",
        security_kb(),
    )


@router.message(F.text == "📋 Белый список", IsBotAdminFilter(ADMINS))
async def security_whitelist(message: types.Message):
    await show_placeholder(
        message,
        "📋 Белый список",
        "Белый список можно подключить через отдельную таблицу разрешённых ID.",
        security_kb(),
    )


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
    old_value = url_yaml.get("antispam_referral_limit")
    url_yaml["antispam_referral_limit"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "antispam_referral_limit", old_value, url_yaml["antispam_referral_limit"])
    await message.answer(
        f"✅ Лимит антифрода изменён на <b>{url_yaml['antispam_referral_limit']}</b> рефералов/час.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()
