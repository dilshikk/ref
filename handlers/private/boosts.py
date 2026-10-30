"""🚀 Голос за канал (буст по ссылке t.me/boost/...) — оплачивается один раз.

Опрос «🗳 Голосование» внутри бота не оплачивается (fallback.py).
- router — кнопка меню и «✅ Я проголосовал» (личка);
- event_router — события бустов из канала (chat_boost / removed_chat_boost).
"""
from aiogram import Bot, F, Router, types
from aiogram.types import InlineKeyboardMarkup
from loguru import logger

from data.settings import get_settings
from filters import private_only
from keyboards.inline_keyboards.actions import BOOST_CHECK_CALLBACK, boost_keyboard, subscribe_keyboard
from services.boosts import (
    BoostReward,
    configured_target,
    hold_note,
    is_tracked_chat,
    reverse_boost,
    reward_boost,
)

router = private_only(Router(name="private.boosts"))
# События бустов приходят из канала, а не из лички
event_router = Router(name="channels.boosts")

_NOT_CONFIGURED = "Голосование за канал пока не настроено."


def _result_text(result: BoostReward) -> str:
    if result.outcome == "rewarded":
        return (
            "🚀 Спасибо за голос за канал!\n\n"
            f"💰 Вам начислено <b>{result.amount}</b> сум.\n\n{hold_note()}"
        )
    if result.outcome == "already":
        return "✅ Награда за голос за канал уже начислялась — она выдаётся один раз."
    if result.outcome == "not_subscribed":
        return (
            "🚀 Спасибо за голос за канал!\n\n"
            "💡 Награда начисляется только подписчикам всех каналов. Подпишитесь, затем откройте "
            "«🚀 Голос за канал» и нажмите «✅ Я проголосовал» 👇"
        )
    if result.outcome == "not_user":
        return "Сначала запустите бота командой /start, затем нажмите «✅ Я проголосовал»."
    return "🚀 Спасибо за голос за канал!"


async def _markup_for(result: BoostReward) -> InlineKeyboardMarkup | None:
    if result.outcome != "not_subscribed":
        return None
    try:
        return await subscribe_keyboard()
    except Exception as e:
        logger.warning(f"Не удалось собрать кнопки каналов: {e}")
        return None


@router.message(F.text == "🚀 Голос за канал")
async def boost_menu(message: types.Message) -> None:
    markup = boost_keyboard()
    if markup is None:
        await message.answer(_NOT_CONFIGURED)
        return
    reward = get_settings().boost_credit
    reward_line = f"\n\n💰 Награда: <b>{reward}</b> сум (один раз)." if reward > 0 else ""
    await message.answer(
        "🚀 <b>Голос за канал</b>\n\n"
        "1) Нажмите «🚀 Проголосовать за канал» и отдайте голос.\n"
        "2) Вернитесь и нажмите «✅ Я проголосовал»."
        f"{reward_line}",
        parse_mode="HTML",
        reply_markup=markup,
    )


@router.callback_query(F.data == BOOST_CHECK_CALLBACK)
async def boost_check(call: types.CallbackQuery, bot: Bot) -> None:
    """Проверка голоса вручную: нужна тем, кто проголосовал до запуска бота или до подписки."""
    target = configured_target()
    if target is None:
        await call.answer(_NOT_CONFIGURED, show_alert=True)
        return
    user_id = call.from_user.id
    try:
        chat = await bot.get_chat(target)
        boosts = await bot.get_user_chat_boosts(chat_id=chat.id, user_id=user_id)
    except Exception as e:
        # Чаще всего: бот не администратор канала
        logger.warning(f"Не удалось проверить буст user={user_id} в {target}: {e}")
        await call.answer("⚠️ Не удалось проверить голос. Попробуйте через минуту.", show_alert=True)
        return

    if not boosts.boosts:
        await call.answer(
            "Голос пока не найден. Нажмите «🚀 Проголосовать за канал», отдайте голос и вернитесь сюда.",
            show_alert=True,
        )
        return

    try:
        result = await reward_boost(user_id, chat.id, boosts.boosts[0].boost_id)
    except Exception as e:
        logger.error(f"Ошибка начисления за буст user={user_id}: {e}")
        await call.answer("⚠️ Не удалось начислить награду. Попробуйте позже.", show_alert=True)
        return

    await call.answer()
    if isinstance(call.message, types.Message):
        await call.message.answer(_result_text(result), parse_mode="HTML", reply_markup=await _markup_for(result))


@event_router.chat_boost()
async def on_chat_boost(event: types.ChatBoostUpdated, bot: Bot) -> None:
    if not is_tracked_chat(event.chat.id, event.chat.username):
        return
    # У буста из розыгрыша пользователя может не быть
    user = getattr(event.boost.source, "user", None)
    if user is None or user.is_bot:
        return

    try:
        result = await reward_boost(user.id, event.chat.id, event.boost.boost_id)
    except Exception as e:
        logger.error(f"Ошибка начисления за буст user={user.id}: {e}")
        return
    logger.info(f"🚀 Буст канала: user={user.id}, итог={result.outcome}, +{result.amount} сум")

    # Второй буст того же человека, заблокированный или не запускавший бота — не пишем
    if result.outcome in ("already", "blocked", "not_user"):
        return
    try:
        await bot.send_message(
            user.id, _result_text(result), parse_mode="HTML", reply_markup=await _markup_for(result)
        )
    except Exception as e:
        logger.warning(f"Не удалось написать user={user.id} о бусте: {e}")


@event_router.removed_chat_boost()
async def on_removed_chat_boost(event: types.ChatBoostRemoved, bot: Bot) -> None:
    try:
        reversed_row = await reverse_boost(event.chat.id, event.boost_id)
    except Exception as e:
        logger.error(f"Ошибка снятия награды за буст {event.boost_id}: {e}")
        return
    if reversed_row is None:
        # Не оплачивался, уже снят или отменён позже срока
        return

    user_id, amount = reversed_row
    logger.info(f"↩️ Буст отменён: user={user_id}, -{amount} сум")
    try:
        await bot.send_message(
            user_id,
            f"↩️ Вы отменили голос за канал.\n\n💸 Начисление <b>{amount}</b> сум снято.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить user={user_id} о снятии за буст: {e}")
