from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def reply_keyboard_for_admin() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💰 Финансы")],
            [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="📢 Каналы и группы")],
            [KeyboardButton(text="🤖 Бот"), KeyboardButton(text="📨 Рассылки")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🔒 Безопасность")],
            [KeyboardButton(text="📋 Логи"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def stats_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Новые пользователи"), KeyboardButton(text="📈 Активность")],
            [KeyboardButton(text="📢 Каналы"), KeyboardButton(text="💳 Финансы")],
            [KeyboardButton(text="🎁 Реферальная статистика"), KeyboardButton(text="📅 Сегодня")],
            [KeyboardButton(text="📆 Неделя"), KeyboardButton(text="🗓 Месяц")],
            [KeyboardButton(text="🌍 За всё время"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def finance_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Балансы"), KeyboardButton(text="⭐ Подписки")],
            [KeyboardButton(text="💵 Пополнения"), KeyboardButton(text="🎁 Бонусы")],
            [KeyboardButton(text="💸 Выплаты"), KeyboardButton(text="📊 Отчёты")],
            [KeyboardButton(text="🆔 Чат выплат")],
            [KeyboardButton(text="⚙️ Тарифы"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def users_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск пользователя"), KeyboardButton(text="📋 Все пользователи")],
            [KeyboardButton(text="⭐ Premium"), KeyboardButton(text="🚫 Заблокированные пользователи")],
            [KeyboardButton(text="👑 Администраторы"), KeyboardButton(text="🎁 Рефералы")],
            [KeyboardButton(text="➕ Выдать подписку"), KeyboardButton(text="💰 Изменить баланс")],
            [KeyboardButton(text="📨 Отправить сообщение"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def channels_groups_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Все"), KeyboardButton(text="➕ Новые")],
            [KeyboardButton(text="🔍 Поиск канала"), KeyboardButton(text="👑 Владельцы")],
            [KeyboardButton(text="⚙️ Настройки каналов"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="🚫 Заблокированные каналы"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def bot_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Главное меню"), KeyboardButton(text="📝 Тексты")],
            [KeyboardButton(text="🌍 Языки"), KeyboardButton(text="🎁 Реферальная система")],
            [KeyboardButton(text="📢 Уведомления"), KeyboardButton(text="⚙️ Конфиг бота")],
            [KeyboardButton(text="🗳 Опросы")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def broadcasts_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Новая"), KeyboardButton(text="📋 История")],
            [KeyboardButton(text="⏳ Запланированные"), KeyboardButton(text="📊 Статистика рассылок")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def settings_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔐 Безопасность"), KeyboardButton(text="🌍 Локализация")],
            [KeyboardButton(text="💰 Тарифы"), KeyboardButton(text="🎁 Реферальные настройки")],
            [KeyboardButton(text="📊 Лимиты"), KeyboardButton(text="🤖 Поведение бота")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def tariffs_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💸 Мин. сумма вывода"), KeyboardButton(text="💰 Макс. сумма вывода")],
            [KeyboardButton(text="🎁 Сумма за приглашение"), KeyboardButton(text="🗳 Сумма за голос")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def security_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚫 Бан пользователей"), KeyboardButton(text="🚫 Бан групп")],
            [KeyboardButton(text="🛡 Антиспам"), KeyboardButton(text="📱 Ограничения")],
            [KeyboardButton(text="📋 Белый список"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def logs_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Пользовательские"), KeyboardButton(text="💰 Финансовые")],
            [KeyboardButton(text="📢 Группы"), KeyboardButton(text="🤖 События бота")],
            [KeyboardButton(text="⚙️ Изменения"), KeyboardButton(text="❌ Ошибки")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def bot_menu_settings_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Команды пользователей"), KeyboardButton(text="🛡 Команды админов")],
            [KeyboardButton(text="🔄 Обновить меню"), KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def subscription_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить канал"), KeyboardButton(text="➖ Удалить канал")],
            [KeyboardButton(text="👁‍🗨 Посмотреть каналы"), KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
