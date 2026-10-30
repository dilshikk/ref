# handlers/private/admin/channels.py
"""📢 Каналы и группы, обязательные подписки.
Заглушки «➕ Новые», «🔍 Поиск канала», «👑 Владельцы», «🚫 Заблокированные каналы»
удалены (#79–#83): добавление каналов — через «📌 Подписки»."""
from typing import Any, cast
from aiogram import Router, types, F
from filters import private_only
from keyboards.reply_keyboards import (
    channels_groups_kb,
    group_settings_kb,
    subscription_kb,
)
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings

from .common import get_channel_totals

router = private_only(Router(name="private.admin.channels"))


@router.message(F.text == "📢 Каналы и группы", IsBotAdminFilter(ADMINS))
async def channels_groups_menu(message: types.Message):
    channels = await get_channel_totals()
    await message.answer(
        "📢 <b>Каналы и группы</b>\n\n"
        f"📋 Подключено каналов: <b>{channels['total_channels']}</b>\n\n"
        "📌 Подписки — добавить или удалить обязательный канал.\n"
        "⚙️ Настройки каналов — группа для приглашений и ссылка голосования.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "📋 Все", IsBotAdminFilter(ADMINS))
async def channels_list_menu(message: types.Message):
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    if not channels:
        await message.answer("📋 Каналы и группы пока не добавлены.", reply_markup=channels_groups_kb())
        return
    lines = [f"• <code>{channel['chat_id']}</code> — {channel['link']}" for channel in channels]
    await message.answer(
        "📋 <b>Все каналы и группы</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "⚙️ Настройки каналов", IsBotAdminFilter(ADMINS))
async def channels_settings_menu(message: types.Message):
    # #82: кнопки для обработчиков из settings.py, которые раньше были недостижимы
    settings = get_settings()
    await message.answer(
        "⚙️ <b>Настройки группы</b>\n\n"
        f"🆔 Группа для приглашений: <code>{settings.group_id or 'не задана'}</code>\n"
        f"📝 Группа логов: <code>{settings.log_group_id or 'не задана'}</code>\n"
        f"👤 Участников для вывода: <b>{settings.group_member_count}</b>\n\n"
        "Выберите, что изменить.",
        parse_mode="HTML",
        reply_markup=group_settings_kb(),
    )


@router.message(F.text == "📈 Статистика", IsBotAdminFilter(ADMINS))
async def channels_statistics_menu(message: types.Message):
    channels = await get_channel_totals()
    await message.answer(
        "📈 <b>Статистика каналов и групп</b>\n\n"
        f"Всего подключено: <b>{channels['total_channels']}</b>",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "📌 Подписки", IsBotAdminFilter(ADMINS))
async def force_subscription(message: types.Message):
    await message.answer(
        "📋 Раздел управления <b>обязательными подписками</b>:",
        parse_mode="HTML",
        reply_markup=subscription_kb(),
    )
