# /www/wwwroot/uz_ibot/keyboards/inline/buttons.py
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from data.settings import get_settings, is_http_url
from loader import db
from utils.boost_link import boost_target

# «✅ Я проголосовал» — проверка буста канала (handlers/private/boosts.py)
BOOST_CHECK_CALLBACK = "boost_check"


# ✅ Универсальная клавиатура подтверждения "Да / Нет"
def action_keyboard(key: str) -> InlineKeyboardMarkup:
    options = {
        "url": ("yes_url", "no"),
        "min_balance": ("yes_min_balance", "no"),
        "invite_reward": ("yes_invite_reward", "no"),
        "payment_chat_id": ("yes_payment_chat_id", "no"),
        "subcr_channel": ("yes_subcr_channel", "no"),
        "group_id": ("yes_group_id", "no"),
        "log_group_id": ("yes_log_group_id", "no"),
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
    url = get_settings().vote_url
    if not is_http_url(url):
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🗳 Проголосовать", url=url)
        ]]
    )


# 🚀 Голос за канал: ссылка на буст и проверка «Я проголосовал»
def boost_keyboard() -> Optional[InlineKeyboardMarkup]:
    url = get_settings().vote_url
    if not is_http_url(url):
        return None
    rows = [[InlineKeyboardButton(text="🚀 Проголосовать за канал", url=url)]]
    # Проверить голос можно только по ссылке на буст (t.me/boost/...)
    if boost_target(url) is not None:
        rows.append([InlineKeyboardButton(text="✅ Я проголосовал", callback_data=BOOST_CHECK_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _channel_buttons() -> list[list[InlineKeyboardButton]]:
    channels = await db.get_all_channels()
    rows: list[list[InlineKeyboardButton]] = []
    for index, channel in enumerate(channels, start=1):
        link = channel["link"]
        if not link or not link.startswith("http"):
            continue  # пропускаем каналы без валидной ссылки
        rows.append([InlineKeyboardButton(text=f"📢 Канал {index}", url=link)])
    return rows


async def subscribe_keyboard_invited(user_id: int) -> InlineKeyboardMarkup:
    rows = await _channel_buttons()
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data=f"subscribe_true_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def subscribe_keyboard() -> InlineKeyboardMarkup:
    rows = await _channel_buttons()
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="subscribe_true")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
