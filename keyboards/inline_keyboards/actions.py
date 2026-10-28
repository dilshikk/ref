# /www/wwwroot/uz_ibot/keyboards/inline/buttons.py
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from data.config import url_yaml
from loader import db


# ✅ Универсальная клавиатура подтверждения "Да / Нет"
def action_keyboard(key: str) -> InlineKeyboardMarkup:
    options = {
        "url": ("yes_url", "no"),
        "min_balance": ("yes_min_balance", "no"),
        "invite_reward": ("yes_invite_reward", "no"),
        "payment_chat_id": ("yes_payment_chat_id", "no"),
        "vote_reward": ("yes_vote_reward", "no"),
        "subcr_channel": ("yes_subcr_channel", "no"), 
        "subcr_chat_id": ("yes_subcr_chat_id", "no"),
        "group_member_count": ("yes_group_member_count", "no"),
        "max_withdraw": ("yes_max_withdraw", "no"),
        "antispam_limit": ("yes_antispam_limit", "no"),
    }

    yes_callback, no_callback = options.get(key, ("yes", "no"))

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data=yes_callback),
            InlineKeyboardButton(text="❌ Нет", callback_data=no_callback),
        ]]
    )


# 🗳 Кнопка "Проголосовать" (ссылка)
def url_button() -> Optional[InlineKeyboardMarkup]:
    url = str(url_yaml.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🗳 Проголосовать", url=url)
        ]]
    )


async def subscribe_keyboard_invited(user_id: int) -> InlineKeyboardMarkup:
    channels = await db.get_all_channels()
    inline_keyboard = []

    for index, channel in enumerate(channels, start=1):
        link = channel["link"]
        if not link or not link.startswith("http"):
            continue  # пропускаем каналы без валидной ссылки
        inline_keyboard.append([
            InlineKeyboardButton(text=f"📢 Канал {index}", url=link)
        ])

    inline_keyboard.append([
        InlineKeyboardButton(text="✅ Я подписался", callback_data=f"subscribe_true_{user_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


async def subscribe_keyboard() -> InlineKeyboardMarkup:
    channels = await db.get_all_channels()
    inline_keyboard = []

    for index, channel in enumerate(channels, start=1):
        link = channel["link"]
        if not link or not link.startswith("http"):
            continue
        inline_keyboard.append([
            InlineKeyboardButton(text=f"📢 Канал {index}", url=link)
        ])

    inline_keyboard.append([
        InlineKeyboardButton(text="✅ Я подписался", callback_data="subscribe_true")
    ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
