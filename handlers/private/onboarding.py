# /www/wwwroot/uz_ibot/handlers/private/onboarding.py
from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from loguru import logger
from typing import Any, cast

from filters import private_only
from data.config import url_yaml, CHANNELS
from keyboards.inline_keyboards.actions import subscribe_keyboard_invited, subscribe_keyboard
from keyboards.reply_keyboards.menu import menu_kb
from loader import db, bot
from services.activity import activity_service
from services.audit_log import audit_log_service

private_router = private_only(Router(name="private.onboarding.start"))
group_router = Router(name="private.onboarding.group")
router = private_router


def _start_pitch(full_name: str) -> str:
    invited_credit = int(url_yaml.get("invited_credit", 0) or 0)
    min_balance = int(url_yaml.get("min_balance", 0) or 0)
    return (
        f"👋 Привет, <b>{full_name}</b>!\n\n"
        "💸 <b>Здесь вы зарабатываете на приглашениях</b>\n"
        f"• За каждого приглашённого: <b>{invited_credit} сум</b>\n"
        f"• Минимальный вывод: <b>{min_balance} сум</b>\n"
        "• Прозрачные выплаты через публичный чат\n\n"
        "🚀 <b>Начните за 30 секунд:</b>\n"
        "1) Откройте «🔗 Моя реферальная ссылка»\n"
        "2) Поделитесь ссылкой\n"
        "3) Проверяйте доход в «💰 Баланс»"
    )


@group_router.message(F.chat.type.in_({"group", "supergroup"}))
async def respond_in_group(message: types.Message) -> None:
    if not message.text:
        return
    text = message.text.lower()
    if "привет" in text or "салом" in text:
        await message.answer("Привет! Чем могу помочь?")
    elif "спасибо" in text or "рахмат" in text:
        await message.answer("Не за что 😊")


async def is_user_subscribed(user_id: int) -> bool:
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    if not channels:
        return True

    for channel in channels:
        channel_id = channel["chat_id"]
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except TelegramBadRequest as e:
            logger.warning(f"TelegramBadRequest для канала {channel_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка проверки подписки {channel_id}: {e}")

    return True


@private_router.message(CommandStart())
async def start(message: types.Message, command: CommandObject, state: FSMContext) -> None:
    user_obj = message.from_user
    if user_obj is None:
        return
    if user_obj.is_bot:
        return

    try:
        await state.clear()
    except Exception as e:
        logger.warning(f"Ошибка при очистке состояния: {e}")

    telegram_id = user_obj.id
    full_name = user_obj.full_name
    username = user_obj.username

    user = await db.select_user(telegram_id=telegram_id)
    if not user:
        await db.add_user(full_name=full_name, username=username, telegram_id=telegram_id)
        await audit_log_service.record_user_action(
            actor_id=telegram_id,
            action="user_registered",
            entity_type="user",
            entity_id=telegram_id,
            new_value={"full_name": full_name, "username": username},
            payload={"source": "start_command"},
        )

    await activity_service.record(
        user_id=telegram_id,
        event="bot_start",
        chat_id=message.chat.id,
        session_id=str(message.message_id),
        payload={"command_args": command.args},
    )

    if await is_user_subscribed(user_id=telegram_id):
        await message.answer(
            _start_pitch(full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
    else:
        if not CHANNELS:
            return

        # command.args теперь не используется для реферала —
        # сохраняем только для корректного показа кнопки подписки
        keyboard = (
            await subscribe_keyboard_invited(int(command.args))
            if command.args and command.args.isdigit()
            else await subscribe_keyboard()
        )
        await message.answer(
            "Чтобы пользоваться ботом, подпишитесь на все каналы 👇",
            reply_markup=keyboard,
        )


@private_router.callback_query(lambda c: c.data.startswith("subscribe_true_"))
async def confirm_subscription_invited(call: types.CallbackQuery) -> None:
    msg = call.message
    if msg is None or call.data is None:
        return

    msg_any = cast(Any, msg)
    await msg_any.delete()
    await activity_service.record(
        user_id=call.from_user.id,
        event="subscription_confirmed",
        chat_id=msg.chat.id,
        session_id=call.id,
        payload={"variant": "invited"},
    )
    if await is_user_subscribed(user_id=call.from_user.id):
        await msg_any.answer(
            _start_pitch(call.from_user.full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
    else:
        referrer_id = int(call.data.split("_")[2])
        await msg_any.answer(
            "❗️Подпишитесь на все каналы, чтобы продолжить 👇",
            reply_markup=await subscribe_keyboard_invited(referrer_id),
        )


@private_router.callback_query(lambda c: c.data == "subscribe_true")
async def confirm_subscription(call: types.CallbackQuery) -> None:
    msg = call.message
    if msg is None:
        return

    msg_any = cast(Any, msg)
    await msg_any.delete()
    await activity_service.record(
        user_id=call.from_user.id,
        event="subscription_confirmed",
        chat_id=msg.chat.id,
        session_id=call.id,
        payload={"variant": "standard"},
    )
    if await is_user_subscribed(user_id=call.from_user.id):
        await msg_any.answer(
            _start_pitch(call.from_user.full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
    else:
        await msg_any.answer(
            "❗️Подпишитесь на все каналы, чтобы продолжить 👇",
            reply_markup=await subscribe_keyboard(),
        )
