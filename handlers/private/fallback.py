# /www/wwwroot/uz_ibot/handlers/private/fallback.py
from typing import Any, cast

from aiogram import Router, types, F
from loguru import logger
from filters import private_only
from keyboards.inline_keyboards.actions import url_button
from data.config import url_yaml
from loader import db

router = private_only(Router(name="private.fallback"))


def _poll_config() -> tuple[str, list[str]]:
    default_question = "Насколько вам удобен наш бот?"
    default_options = ["Отлично", "Нормально", "Нужно улучшить"]

    question = str(url_yaml.get("poll_question") or default_question).strip()
    raw_options = url_yaml.get("poll_options")

    if isinstance(raw_options, list):
        options = [str(x).strip() for x in raw_options if str(x).strip()]
    elif isinstance(raw_options, str):
        options = [part.strip() for part in raw_options.split("|") if part.strip()]
    else:
        options = []

    if len(options) < 2:
        options = default_options

    return question, options[:10]


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
    await message.answer("Спасибо за голос! Ваше мнение помогает улучшать бота.")


@router.poll_answer()
async def poll_answer_handler(poll_answer: types.PollAnswer) -> None:
    if not poll_answer.option_ids:
        return
    if poll_answer.user is None:
        return
    option_index = int(poll_answer.option_ids[0])
    await db.upsert_poll_vote_answer(
        poll_id=poll_answer.poll_id,
        user_id=poll_answer.user.id,
        selected_option=option_index,
    )


@router.message()
async def fallback_handler(message: types.Message) -> None:
    markup = url_button()
    if markup:
        await message.answer("Оставьте свой голос👇", reply_markup=markup)
    else:
        logger.warning("url_button: ссылка голосования не задана или невалидна в url.yml")
        await message.answer("Я пока не понял команду. Выберите действие в меню.")
