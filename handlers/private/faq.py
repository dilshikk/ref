from aiogram import Router, types
from aiogram.filters import Command

from filters import private_only
from data.settings import custom_text, get_settings

router = private_only(Router(name="private.faq"))

# Обработчик /start отсюда убран: onboarding подключён раньше и всегда
# забирал команду себе, так что этот код никогда не выполнялся.


def default_help_text() -> str:
    """
    Текст /help по умолчанию. #105: кнопки названы так же, как в меню
    (keyboards/reply_keyboards/menu.py), без несуществующих «🆘 Поддержка»
    и «напиши любое сообщение».
    """
    settings = get_settings()
    boost_reward = (
        f" — получишь <b>+{settings.boost_credit} сум</b> (один раз)" if settings.boost_credit > 0 else ""
    )
    return (
        "📖 <b>Как работает бот?</b>\n\n"

        "1️⃣ <b>Получи реферальную ссылку</b>\n"
        "   Нажми «🔗 Моя реферальная ссылка» — получишь личную инвайт-ссылку в группу\n\n"

        "2️⃣ <b>Приглашай друзей</b>\n"
        f"   Отправь ссылку друзьям. За каждого вступившего — <b>+{settings.invited_credit} сум</b>\n\n"

        "3️⃣ <b>Следи за балансом</b>\n"
        "   Нажми «💰 Баланс» — увидишь, сколько заработал\n\n"

        "4️⃣ <b>Выводи деньги</b>\n"
        "   Нажми «💸 Вывести средства»\n"
        f"   Минимум для вывода — <b>{settings.min_balance} сум</b>\n"
        f"   Перед выводом нужно добавить <b>{settings.group_member_count}</b> участников в группу\n"
        "   Вывод на карту или телефон\n\n"

        "👥 <b>Мои приглашённые</b>\n"
        "   Последние приглашённые, сколько из них активны и сколько заработано\n\n"

        "📜 <b>История выплат</b>\n"
        "   Все твои заявки на вывод и их статус\n\n"

        "📢 <b>Чат выплат</b>\n"
        "   Публичный канал с подтверждениями выплат\n\n"

        "🚀 <b>Голос за канал</b>\n"
        f"   Отдай голос (буст) за наш канал{boost_reward}\n\n"

        "🗳 <b>Голосование</b>\n"
        "   Короткий опрос о боте — помогает нам стать лучше\n\n"

        "🏆 <b>Топ рефералов</b>\n"
        "   Команда /top10 — посмотри, кто лидирует\n\n"

        "❓ Есть вопросы? Нажми «📞 Поддержка»"
    )


@router.message(Command("help"))
async def help_command(message: types.Message) -> None:
    # #88: текст из «📝 Тексты», если админ его задал
    text = custom_text("help") or default_help_text()
    await message.answer(text, parse_mode="HTML")
