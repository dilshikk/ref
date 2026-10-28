# /www/wwwroot/uz_ibot/handlers/private/faq.py
from aiogram import Router, types
from aiogram.filters import Command

from filters import private_only
from data.settings import get_settings

router = private_only(Router(name="private.faq"))

# Обработчик /start отсюда убран: onboarding подключён раньше и всегда
# забирал команду себе, так что этот код никогда не выполнялся.


@router.message(Command("help"))
async def help_command(message: types.Message) -> None:
    settings = get_settings()

    text = (
        "📖 <b>Как работает бот?</b>\n\n"

        "1️⃣ <b>Получи реферальную ссылку</b>\n"
        "   Нажми «🔗 Моя реферальная ссылка» — получишь личную инвайт-ссылку в группу\n\n"

        "2️⃣ <b>Приглашай друзей</b>\n"
        f"   Отправь ссылку друзьям. За каждого вступившего — <b>+{settings.invited_credit} сум</b>\n\n"

        "3️⃣ <b>Следи за балансом</b>\n"
        "   Нажми «💰 Баланс» — увидишь сколько заработал\n\n"

        "4️⃣ <b>Выводи деньги</b>\n"
        f"   Минимум для вывода — <b>{settings.min_balance} сум</b>\n"
        f"   Перед выводом нужно добавить <b>{settings.group_member_count}</b> участников в группу\n"
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
