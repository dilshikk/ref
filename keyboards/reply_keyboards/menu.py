from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅ Назад")]],
        resize_keyboard=True,
    )


def menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔗 Моя реферальная ссылка")],
            [KeyboardButton(text="👥 Мои приглашённые"), KeyboardButton(text="💰 Баланс")],
            [KeyboardButton(text="💸 Вывести средства"), KeyboardButton(text="📜 История выплат")],
            [KeyboardButton(text="📞 Поддержка"), KeyboardButton(text="⚙ Настройки")],
            [KeyboardButton(text="📢 Чат выплат")],
            [KeyboardButton(text="🗳 Голосование")],
        ],
        resize_keyboard=True,
    )
