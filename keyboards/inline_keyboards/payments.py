from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_money() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="💸 Вывести деньги", callback_data="withdraw_money")
        ]]
    )


def approval_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data="confirm_card"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_card"),
        ]]
    )


def card_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_card"),
            InlineKeyboardButton(text="❌ Изменить", callback_data="cancel_card"),
        ]]
    )


def payment_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Переведено", callback_data=f"payment_done_{user_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"payment_cancel_{user_id}"),
        ]]
    )
