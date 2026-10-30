# /www/wwwroot/uz_ibot/handlers/private/fallback.py
"""Опрос «🗳 Голосование» и тихая обработка непонятных сообщений.

Голос в опросе внутри бота НЕ оплачивается: он нужен только для обратной связи
и статистики в «⚙️ Настройки» → «🗳 Опрос». Оплачивается голос (буст) за канал
по ссылке «Проголосовать» — см. handlers/private/boosts.py.

Неизвестные команды и любой непонятный текст бот теперь ИГНОРИРУЕТ (молчит).
Раньше на каждое такое сообщение приходило «Оставьте свой голос👇» с кнопками
буста — в том числе админу, который просто ввёл второй поисковый запрос
или ответил после закрытой формы.
"""
from typing import Any, cast

from aiogram import Bot, Router, types, F
from loguru import logger
from filters import private_only
from data.settings import get_settings
from loader import db

router = private_only(Router(name="private.fallback"))

_THANKS = "Спасибо за голос! Ваше мнение помогает улучшать бота."


def _poll_config() -> tuple[str, list[str]]:
    # Разбор вопроса и вариантов (с умолчаниями и лимитом Telegram) — в get_settings()
    settings = get_settings()
    return settings.poll_question, list(settings.poll_options)


@router.message(F.text == "🗳 Голосование")
async def telegram_poll_vote(message: types.Message) -> None:
    question, options = _poll_config()
    poll_msg = await message.answer_poll(
        question=question,
        options=cast(Any, options),
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    poll = poll_msg.poll
    if poll is not None and message.from_user is not None:
        await db.insert_poll_instance(
            poll_id=poll.id,
            question=question,
            options=options,
            source="private",
            created_by=message.from_user.id,
        )
    # «Спасибо за голос» приходит в poll_answer_handler после выбора варианта.


# poll_answer не проходит через фильтр private_only (он ставится только на сообщения
# и кнопки), поэтому голоса доходят сюда из любого чата.
@router.poll_answer()
async def poll_answer_handler(poll_answer: types.PollAnswer, bot: Bot) -> None:
    if not poll_answer.option_ids:
        # Пользователь отозвал голос
        return
    user = poll_answer.user
    if user is None:
        return

    await db.upsert_poll_vote_answer(
        poll_id=poll_answer.poll_id,
        user_id=user.id,
        selected_option=int(poll_answer.option_ids[0]),
    )

    try:
        await bot.send_message(user.id, _THANKS)
    except Exception as e:
        logger.warning(f"Не удалось поблагодарить за голос user={user.id}: {e}")


def _log_ignored(message: types.Message, kind: str) -> None:
    user_id = message.from_user.id if message.from_user else "?"
    logger.debug(f"Игнорирую {kind} от user={user_id}: {(message.text or message.content_type)!r}")


# Незнакомая команда (/что-то) — молчим
@router.message(F.text.startswith("/"))
async def unknown_command(message: types.Message) -> None:
    _log_ignored(message, "неизвестную команду")


# Любое другое непонятное сообщение — тоже молчим. Обработчик нужен, чтобы
# сообщение считалось обработанным и не шло дальше по цепочке.
@router.message()
async def fallback_handler(message: types.Message) -> None:
    _log_ignored(message, "непонятное сообщение")
