# /www/wwwroot/uz_ibot/handlers/private/onboarding.py
from aiogram import Router, types, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from loguru import logger
from typing import Any, cast

from filters import private_only
from data.config import url_yaml
from keyboards.inline_keyboards.actions import subscribe_keyboard_invited, subscribe_keyboard
from keyboards.reply_keyboards.menu import menu_kb
from loader import db
from services.activity import activity_service
from services.audit_log import audit_log_service
from services.subscription import is_user_subscribed

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

    if await is_user_subscribed(user_id=telegram_id, use_cache=False):
        await message.answer(
            _start_pitch(full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
        return

    # Неподписанный пользователь всегда получает кнопки подписки
    # (раньше бот молчал, если в url.yml был пустой список channels)
    keyboard = (
        await subscribe_keyboard_invited(int(command.args))
        if command.args and command.args.isdigit()
        else await subscribe_keyboard()
    )
    await message.answer(
        "Чтобы пользоваться ботом, подпишитесь на все каналы 👇",
        reply_markup=keyboard,
    )


async def _handle_subscription_check(call: types.CallbackQuery, variant: str, referrer_id: int | None) -> None:
    msg = call.message
    if msg is None:
        await call.answer()
        return

    msg_any = cast(Any, msg)
    await activity_service.record(
        user_id=call.from_user.id,
        event="subscription_confirmed",
        chat_id=msg.chat.id,
        session_id=call.id,
        payload={"variant": variant},
    )

    if not await is_user_subscribed(user_id=call.from_user.id, use_cache=False):
        # Сообщение не удаляем — пользователь видит кнопки каналов и пробует снова
        await call.answer("❗️ Вы подписались не на все каналы", show_alert=True)
        return

    await call.answer()
    try:
        await msg_any.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение подписки: {e}")
    await msg_any.answer(
        _start_pitch(call.from_user.full_name),
        parse_mode="HTML",
        reply_markup=menu_kb(),
    )


@private_router.callback_query(F.data.startswith("subscribe_true_"))
async def confirm_subscription_invited(call: types.CallbackQuery) -> None:
    raw = (call.data or "").split("_")[-1]
    referrer_id = int(raw) if raw.isdigit() else None
    await _handle_subscription_check(call, "invited", referrer_id)


@private_router.callback_query(F.data == "subscribe_true")
async def confirm_subscription(call: types.CallbackQuery) -> None:
    await _handle_subscription_check(call, "standard", None)
