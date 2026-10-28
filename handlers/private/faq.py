# /www/wwwroot/uz_ibot/handlers/private/faq.py
from aiogram import Router, types, F
from aiogram.filters import Command
from loguru import logger

from filters import private_only
from data.config import url_yaml
from keyboards.reply_keyboards.menu import menu_kb

router = private_only(Router(name="private.faq"))


@router.message(Command("start"))
async def start_command(message: types.Message) -> None:
    invited_credit: int = url_yaml.get("invited_credit", 0)
    min_balance: int = url_yaml.get("min_balance", 0)

    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"🤑 Зарабатывай деньги, приглашая друзей!\n\n"
        f"💡 За каждого приглашённого друга — <b>{invited_credit} сум</b>\n"
        f"💸 Минимум для вывода — <b>{min_balance} сум</b>\n\n"
        f"👇 Выбери действие в меню:",
        parse_mode="HTML",
        reply_markup=menu_kb(),
    )


@router.message(Command("help"))
async def help_command(message: types.Message) -> None:
    invited_credit: int = url_yaml.get("invited_credit", 0)
    min_balance: int = url_yaml.get("min_balance", 0)
    group_member_count: int = url_yaml.get("group_member_count", 3)

    text = (
        "📖 <b>Как работает бот?</b>\n\n"

        "1️⃣ <b>Получи реферальную ссылку</b>\n"
        "   Нажми «🔗 Моя реферальная ссылка» — получишь личную инвайт-ссылку в группу\n\n"

        "2️⃣ <b>Приглашай друзей</b>\n"
        f"   Отправь ссылку друзьям. За каждого вступившего — <b>+{invited_credit} сум</b>\n\n"

        "3️⃣ <b>Следи за балансом</b>\n"
        "   Нажми «💰 Баланс» — увидишь сколько заработал\n\n"

        "4️⃣ <b>Выводи деньги</b>\n"
        f"   Минимум для вывода — <b>{min_balance} сум</b>\n"
        f"   Перед выводом нужно добавить <b>{group_member_count}</b> участников в группу\n"
        "   Вывод на карту или телефон\n\n"

        "🏆 <b>Топ рефералов</b>\n"
        "   Команда /top10 — посмотри кто лидирует\n\n"

        "👥 <b>Мои приглашённые</b>\n"
        "   Нажми «👥 Мои приглашённые» — список тех кого привёл\n\n"

        "🗳 <b>Голосование</b>\n"
        "   Напиши любое сообщение — получишь ссылку для голосования\n\n"

        "❓ Есть вопросы? Нажми «🆘 Поддержка»"
    )

    await message.answer(text, parse_mode="HTML")
