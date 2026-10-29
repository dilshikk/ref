# handlers/private/admin/settings.py
"""⚙️ Настройки: ссылки и ID группы, участники для вывода, URL голосования.
Заглушки «🔐 Безопасность» (дубль), «🌍 Локализация», «🤖 Поведение бота» удалены (#89, #91, #93)."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import (
    group_settings_kb,
    settings_kb,
)
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS, url_yaml
from data.settings import get_settings
from utils.yaml_io import save_url_yaml

from .common import cancel_kb, forms_router, record_setting_change

router = private_only(Router(name="private.admin.settings"))

# Числовые настройки показываем через get_settings() — те же значения, по которым
# работает бот (#49). url_yaml остаётся для записи и для текстовых полей.
# После сохранения возвращаем меню настроек группы, а не главное меню.


@router.message(F.text == "⚙️ Настройки", IsBotAdminFilter(ADMINS))
async def settings_menu(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "💰 Тарифы — суммы вывода и награды.\n"
        "🎁 Реферальные настройки — награда и антифрод.\n"
        "📊 Лимиты — текущие ограничения.\n"
        "⚙️ Настройки каналов — группа и ссылка голосования.",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "📊 Лимиты", IsBotAdminFilter(ADMINS))
async def settings_limits_menu(message: types.Message):
    settings = get_settings()
    await message.answer(
        "📊 <b>Лимиты</b>\n\n"
        f"Антифрод: <b>{settings.antispam_referral_limit}</b> в час, "
        f"<b>{settings.antispam_referral_daily_limit}</b> в сутки\n"
        f"Участников для вывода: <b>{settings.group_member_count}</b>\n\n"
        "Изменить антифрод — «🔒 Безопасность» → «🛡 Антиспам».\n"
        "Изменить число участников — «⚙️ Настройки каналов».",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "🔗 Ссылка группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_channel(message: types.Message):
    value = url_yaml.get("subcr_channel", "не задана")
    await message.answer(
        f"🔗 Текущая ссылка группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("subcr_channel"),
    )


@router.callback_query(F.data == "yes_subcr_channel", IsBotAdminFilter(ADMINS))
async def confirm_subcr_channel(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую ссылку группы.\nСейчас: {url_yaml.get('subcr_channel', 'не задана')}\n"
        "Пример: <code>https://t.me/+XXXXXXXXXX</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_subcr_channel)


@forms_router.message(AdminFlow.set_subcr_channel, IsBotAdminFilter(ADMINS))
async def update_subcr_channel(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("https://t.me/"):
        await message.answer(
            "❌ Неверный формат!\nПример: <code>https://t.me/+XXXXXXXXXX</code>",
            parse_mode="HTML",
        )
        return
    old_value = url_yaml.get("subcr_channel", "не задана")
    url_yaml["subcr_channel"] = text
    save_url_yaml()
    await record_setting_change(user.id, "subcr_channel", old_value, url_yaml["subcr_channel"])
    await message.answer(
        f"✅ Ссылка группы обновлена!\n🔗 <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


@router.message(F.text == "🆔 ID группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_chat_id(message: types.Message):
    value = url_yaml.get("subcr_chat_id", "не задан")
    await message.answer(
        f"🆔 Текущий ID группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("subcr_chat_id"),
    )


@router.callback_query(F.data == "yes_subcr_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_subcr_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый chat_id группы.\nСейчас: {url_yaml.get('subcr_chat_id', 'не задан')}\n"
        "Пример: <code>-1001234567890</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_subcr_chat_id)


@forms_router.message(AdminFlow.set_subcr_chat_id, IsBotAdminFilter(ADMINS))
async def update_subcr_chat_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not (text.startswith("-100") and text[1:].isdigit()):
        await message.answer(
            "❌ Неверный формат!\nПример: <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return
    old_value = url_yaml.get("subcr_chat_id", "не задан")
    url_yaml["subcr_chat_id"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "subcr_chat_id", old_value, url_yaml["subcr_chat_id"])
    await message.answer(
        f"✅ ID группы обновлён: <code>{text}</code>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


@router.message(F.text == "👤 Участников для вывода", IsBotAdminFilter(ADMINS))
async def edit_group_member_count(message: types.Message):
    value = get_settings().group_member_count
    await message.answer(
        f"👥 Текущее кол-во участников для вывода: <b>{value}</b>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("group_member_count"),
    )


@router.callback_query(F.data == "yes_group_member_count", IsBotAdminFilter(ADMINS))
async def confirm_group_member_count(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новое кол-во участников.\nСейчас: {get_settings().group_member_count}\n"
        "Пример: <code>5</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_group_member_count)


@forms_router.message(AdminFlow.set_group_member_count, IsBotAdminFilter(ADMINS))
async def update_group_member_count(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>5</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("group_member_count")
    url_yaml["group_member_count"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "group_member_count", old_value, url_yaml["group_member_count"])
    await message.answer(
        f"✅ Кол-во участников для вывода изменено на <b>{url_yaml['group_member_count']}</b>.",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


@router.message(F.text == "✏️ Изменить ссылку (URL)", IsBotAdminFilter(ADMINS))
async def edit_url(message: types.Message):
    value = url_yaml.get("url", "не задан")
    await message.answer(
        f"🌐 Текущая ссылка голосования:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("url"),
    )


@router.callback_query(F.data == "yes_url", IsBotAdminFilter(ADMINS))
async def confirm_url_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый URL.\nСейчас: {url_yaml.get('url', 'не задан')}",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_url)


@forms_router.message(AdminFlow.set_url, IsBotAdminFilter(ADMINS))
async def update_url(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("http"):
        await message.answer("❌ Введите корректную ссылку (начиная с http или https):", parse_mode="HTML")
        return
    old_value = url_yaml.get("url", "")
    url_yaml["url"] = text
    save_url_yaml()
    await record_setting_change(user.id, "url", old_value, url_yaml["url"])
    await message.answer(
        f"✅ URL обновлён: <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()
