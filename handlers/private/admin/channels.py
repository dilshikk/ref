# handlers/private/admin/channels.py
"""📢 Каналы и группы, обязательные подписки."""
from typing import Any, cast
from aiogram import Router, types, F
from filters import private_only
from keyboards.reply_keyboards import (
    channels_groups_kb,
    subscription_kb,
)
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS

from .common import get_channel_totals

router = private_only(Router(name="private.admin.channels"))


@router.message(F.text == "📢 Каналы и группы", IsBotAdminFilter(ADMINS))
async def channels_groups_menu(message: types.Message):
    channels = await get_channel_totals()
    await message.answer(
        "📢 <b>Каналы и группы</b>\n\n"
        f"📋 Подключено каналов: <b>{channels['total_channels']}</b>\n\n"
        "Управление обязательными подписками и групповыми параметрами.",
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


@router.message(F.text == "➕ Новые", IsBotAdminFilter(ADMINS))
async def channels_new_menu(message: types.Message):
    await message.answer(
        "➕ <b>Добавление нового канала или группы</b>\n\n"
        "Используйте текущее меню подписок для добавления chat_id и ссылки.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "🔍 Поиск канала", IsBotAdminFilter(ADMINS))
async def channels_search_menu(message: types.Message):
    await message.answer(
        "🔍 <b>Поиск</b>\n\n"
        "Для каналов и групп пока доступен ручной поиск по chat_id через список и настройки.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "👑 Владельцы", IsBotAdminFilter(ADMINS))
async def channels_owners_menu(message: types.Message):
    await message.answer(
        "👑 <b>Владельцы</b>\n\n"
        "Информацию о владельцах можно добавить отдельным полем в таблицу каналов.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "⚙️ Настройки каналов", IsBotAdminFilter(ADMINS))
async def channels_settings_menu(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки каналов и групп</b>\n\n"
        "Откройте меню подписок или настройки группы для изменения параметров.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
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


@router.message(F.text == "🚫 Заблокированные каналы", IsBotAdminFilter(ADMINS))
async def channels_blocked_menu(message: types.Message):
    await message.answer(
        "🚫 <b>Заблокированные каналы и группы</b>\n\n"
        "Этот раздел можно использовать для ручного blacklist-реестра, если он появится в базе.",
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
