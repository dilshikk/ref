from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def approval_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_card"),
            InlineKeyboardButton(text="❌ Изменить", callback_data="cancel_card"),
        ]]
    )
