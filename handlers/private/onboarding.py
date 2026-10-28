# /www/wwwroot/uz_ibot/handlers/private/onboarding.py
import html
from typing import Any, Awaitable, cast

from aiogram import Router, types, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

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

_SUBSCRIBE_TEXT = "Чтобы пользоваться ботом, подпишитесь на все каналы 👇"


def _start_pitch(full_name: str) -> str:
    invited_credit = int(url_yaml.get("invited_credit", 0) or 0)
    min_balance = int(url_yaml.get("min_balance", 0) or 0)
    # Имя экранируем: символы < > & в имени ломали HTML, Telegram отклонял
    # сообщение, и пользователь на /start не получал ничего
    return (
        f"👋 Привет, <b>{html.escape(full_name)}</b>!\n\n"
        "💸 <b>Здесь вы зарабатываете на приглашениях</b>\n"
        f"• За каждого приглашённого: <b>{invited_credit} сум</b>\n"
        f"• Минимальный вывод: <b>{min_balance} сум</b>\n"
        "• Прозрачные выплаты через публичный чат\n\n"
        "🚀 <b>Начните за 30 секунд:</b>\n"
        "1) Откройте «🔗 Моя реферальная ссылка»\n"
        "2) Поделитесь ссылкой\n"
        "3) Проверяйте доход в «💰 Баланс»"
    )


async def _best_effort(step: Awaitable[Any], what: str, user_id: int) -> None:
    """Вспомогательные шаги (регистрация, статистика, аудит) не должны мешать ответу на /start."""
    try:
        await step
    except Exception as e:
        logger.error(f"/start: не удалось выполнить «{what}» user={user_id}: {e}")


async def _register_user(user_obj: types.User) -> None:
    user = await db.select_user(telegram_id=user_obj.id)
    if user:
        return
    await db.add_user(full_name=user_obj.full_name, username=user_obj.username, telegram_id=user_obj.id)
    await audit_log_service.record_user_action(
        actor_id=user_obj.id,
        action="user_registered",
        entity_type="user",
        entity_id=user_obj.id,
        new_value={"full_name": user_obj.full_name, "username": user_obj.username},
        payload={"source": "start_command"},
    )


async def _check_subscription(user_id: int) -> bool:
    # Как и в SubscriptionMiddleware: если проверить не получилось, пускаем,
    # а не оставляем человека без ответа
    try:
        return await is_user_subscribed(user_id=user_id, use_cache=False)
    except Exception as e:
        logger.error(f"/start: проверка подписки не удалась user={user_id}: {e}")
        return True


async def _subscription_keyboard(args: str | None) -> InlineKeyboardMarkup:
    referrer_id = int(args) if args and args.isdigit() else None
    try:
        if referrer_id is not None:
            return await subscribe_keyboard_invited(referrer_id)
        return await subscribe_keyboard()
    except Exception as e:
        logger.error(f"/start: не удалось собрать кнопки каналов: {e}")
        callback = f"subscribe_true_{referrer_id}" if referrer_id is not None else "subscribe_true"
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Я подписался", callback_data=callback)
        ]])


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
    if user_obj is None or user_obj.is_bot:
        return

    try:
        await state.clear()
    except Exception as e:
        logger.warning(f"Ошибка при очистке состояния: {e}")

    telegram_id = user_obj.id
    await _best_effort(_register_user(user_obj), "регистрация", telegram_id)
    await _best_effort(
        activity_service.record(
            user_id=telegram_id,
            event="bot_start",
            chat_id=message.chat.id,
            session_id=str(message.message_id),
            payload={"command_args": command.args},
        ),
        "запись активности",
        telegram_id,
    )

    if await _check_subscription(telegram_id):
        await message.answer(
            _start_pitch(user_obj.full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
        return

    # Неподписанный пользователь всегда получает кнопки подписки
    await message.answer(_SUBSCRIBE_TEXT, reply_markup=await _subscription_keyboard(command.args))


async def _handle_subscription_check(call: types.CallbackQuery, variant: str, referrer_id: int | None) -> None:
    msg = call.message
    if msg is None:
        await call.answer()
        return

    msg_any = cast(Any, msg)
    await _best_effort(
        activity_service.record(
            user_id=call.from_user.id,
            event="subscription_confirmed",
            chat_id=msg.chat.id,
            session_id=call.id,
            payload={"variant": variant},
        ),
        "запись активности",
        call.from_user.id,
    )

    if not await _check_subscription(call.from_user.id):
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
