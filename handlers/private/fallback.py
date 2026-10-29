# /www/wwwroot/uz_ibot/handlers/private/fallback.py
from typing import Any, cast

from aiogram import Bot, Router, types, F
from loguru import logger
from filters import private_only
from keyboards.inline_keyboards.actions import subscribe_keyboard, url_button
from data.config import ADMINS
from data.settings import get_settings
from loader import db
from services.subscription import is_user_subscribed_strict

router = private_only(Router(name="private.fallback"))

_THANKS = "Спасибо за голос! Ваше мнение помогает улучшать бота."


def _poll_config() -> tuple[str, list[str]]:
    # Разбор вопроса и вариантов (с умолчаниями и лимитом Telegram) — в get_settings()
    settings = get_settings()
    return settings.poll_question, list(settings.poll_options)


def _vote_credit() -> int:
    return get_settings().vote_credit


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
    # «Спасибо за голос» больше не отправляется здесь: человек ещё не проголосовал.
    # Благодарность (и награда) приходят в poll_answer_handler после выбора варианта.


async def _may_reward(user_id: int) -> bool:
    """
    Награда за голос — только подписанным на все каналы (#18). poll_answer не проходит
    через SubscriptionMiddleware (у PollAnswer нет from_user и чата), поэтому
    проверяем здесь. Проверка строгая: если Telegram не ответил, денег не даём.
    """
    if user_id in ADMINS:
        return True
    return await is_user_subscribed_strict(user_id)


# poll_answer не проходит через фильтр private_only (он ставится только на сообщения
# и кнопки), поэтому голоса доходят сюда из любого чата.
@router.poll_answer()
async def poll_answer_handler(poll_answer: types.PollAnswer, bot: Bot) -> None:
    if not poll_answer.option_ids:
        # Пользователь отозвал голос — награду не трогаем, повторно она всё равно не начислится
        return
    user = poll_answer.user
    if user is None:
        return

    option_index = int(poll_answer.option_ids[0])
    await db.upsert_poll_vote_answer(
        poll_id=poll_answer.poll_id,
        user_id=user.id,
        selected_option=option_index,
    )

    reward = _vote_credit()
    rewarded = None
    reply_markup = None
    text = _THANKS
    if reward > 0 and not await db.is_user_blocked(user.id):
        if await _may_reward(user.id):
            try:
                rewarded = await db.reward_vote_once(poll_answer.poll_id, user.id, reward)
            except Exception as e:
                logger.error(f"Ошибка начисления за голос [user={user.id}, poll={poll_answer.poll_id}]: {e}")
        else:
            logger.info(f"🗳 Голос без подписки: user={user.id}, награда не начислена")
            text = (
                f"{_THANKS}\n\n"
                "💡 Награда за голос начисляется только подписчикам всех каналов. "
                "Подпишитесь и проголосуйте ещё раз (отмените голос и выберите вариант снова) 👇"
            )
            try:
                reply_markup = await subscribe_keyboard()
            except Exception as e:
                logger.warning(f"Не удалось собрать кнопки каналов: {e}")

    if rewarded:
        text = f"{_THANKS}\n\n💰 Вам начислено <b>{int(rewarded['reward_sum'] or 0)}</b> сум."
        logger.info(f"🗳 Награда за голос: user={user.id}, +{rewarded['reward_sum']} сум")

    try:
        await bot.send_message(user.id, text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Не удалось поблагодарить за голос user={user.id}: {e}")


@router.message()
async def fallback_handler(message: types.Message) -> None:
    markup = url_button()
    if markup:
        await message.answer("Оставьте свой голос👇", reply_markup=markup)
    else:
        logger.warning("url_button: ссылка голосования не задана или невалидна в url.yml")
        await message.answer("Я пока не понял команду. Выберите действие в меню.")
