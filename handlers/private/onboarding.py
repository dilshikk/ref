# /www/wwwroot/uz_ibot/handlers/private/onboarding.py
import html
from typing import Any, Awaitable, cast

from aiogram import Router, types, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from filters import private_only
from data.settings import custom_text, get_settings
from keyboards.inline_keyboards.actions import subscribe_keyboard_invited, subscribe_keyboard
from keyboards.reply_keyboards.menu import menu_kb
from loader import db
from services.activity import activity_service
from services.audit_log import audit_log_service
from services.subscription import get_subscription_status
from utils.subscription_status import SubscriptionStatus

private_router = private_only(Router(name="private.onboarding.start"))
router = private_router

_SUBSCRIBE_TEXT = "Чтобы пользоваться ботом, подпишитесь на все каналы 👇"
_UNKNOWN_TEXT = (
    "⚠️ Не удалось проверить подписку на каналы. "
    "Подпишитесь на все каналы и нажмите «Я подписался» через минуту 👇"
)


def _start_pitch(full_name: str) -> str:
    # #88: текст из «📝 Тексты», если админ его задал ({name} — имя пользователя)
    custom = custom_text("start", full_name)
    if custom:
        return custom
    # get_settings(): нечисловое значение в url.yml больше не роняет /start (#22)
    settings = get_settings()
    # Имя экранируем: символы < > & в имени ломали HTML, Telegram отклонял
    # сообщение, и пользователь на /start не получал ничего
    return (
        f"👋 Привет, <b>{html.escape(full_name)}</b>!\n\n"
        "💸 <b>Здесь вы зарабатываете на приглашениях</b>\n"
        f"• За каждого приглашённого: <b>{settings.invited_credit} сум</b>\n"
        f"• Минимальный вывод: <b>{settings.min_balance} сум</b>\n"
        "• Прозрачные выплаты через публичный чат\n\n"
        "🚀 <b>Начните за 30 секунд:</b>\n"
        "1) Откройте «🔗 Моя реферальная ссылка»\n"
        "2) Поделитесь ссылкой\n"
        "3) Проверяйте доход в «💰 Баланс»"
    )


def _parse_referrer(args: str | None, user_id: int) -> int | None:
    """ID пригласившего из /start <id>. Своя ссылка и мусор (from_group и т.п.) игнорируются."""
    if not args or not args.isdigit():
        return None
    referrer_id = int(args)
    return referrer_id if referrer_id != user_id else None


async def _send_referrer_group_link(message: types.Message, referrer_id: int | None) -> None:
    """
    Приглашённый пришёл по ссылке на бота (/start <id>). Раньше этот id никак не
    использовался, и пригласивший ничего не получал. Начисление происходит только
    при вступлении в группу по персональной ссылке — поэтому даём именно ссылку
    пригласившего. Все проверки (дубли, антифрод, снятие при выходе) сохраняются.
    """
    if referrer_id is None:
        return
    try:
        if not await db.select_user(telegram_id=referrer_id):
            return
        link = await db.get_user_invite_link(referrer_id)
    except Exception as e:
        logger.warning(f"/start: не удалось получить ссылку пригласившего {referrer_id}: {e}")
        return
    if not link:
        return
    await message.answer(
        "👥 Вас пригласил друг. Вступите в группу по этой ссылке 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➕ Вступить в группу", url=link)
        ]]),
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


async def _check_subscription(user_id: int) -> SubscriptionStatus:
    # Сбой проверки больше не засчитывается как подписка (#24). Человек не остаётся
    # без ответа: получает кнопки каналов и может повторить проверку.
    return await get_subscription_status(user_id=user_id, use_cache=False)


async def _subscription_keyboard(referrer_id: int | None) -> InlineKeyboardMarkup:
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
    referrer_id = _parse_referrer(command.args, telegram_id)
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

    status = await _check_subscription(telegram_id)
    if status is SubscriptionStatus.SUBSCRIBED:
        await message.answer(
            _start_pitch(user_obj.full_name),
            parse_mode="HTML",
            reply_markup=menu_kb(),
        )
        await _send_referrer_group_link(message, referrer_id)
        return

    # Неподписанный (или непроверенный) пользователь всегда получает кнопки подписки
    text = _UNKNOWN_TEXT if status is SubscriptionStatus.UNKNOWN else _SUBSCRIBE_TEXT
    await message.answer(text, reply_markup=await _subscription_keyboard(referrer_id))


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
            payload={"variant": variant, "referrer_id": referrer_id},
        ),
        "запись активности",
        call.from_user.id,
    )

    status = await _check_subscription(call.from_user.id)
    if status is not SubscriptionStatus.SUBSCRIBED:
        # Сообщение не удаляем — пользователь видит кнопки каналов и пробует снова
        alert = (
            "⚠️ Не удалось проверить подписку. Попробуйте через минуту."
            if status is SubscriptionStatus.UNKNOWN
            else "❗️ Вы подписались не на все каналы"
        )
        await call.answer(alert, show_alert=True)
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
    await _send_referrer_group_link(msg_any, referrer_id)


@private_router.callback_query(F.data.startswith("subscribe_true_"))
async def confirm_subscription_invited(call: types.CallbackQuery) -> None:
    raw = (call.data or "").split("_")[-1]
    referrer_id = _parse_referrer(raw, call.from_user.id)
    await _handle_subscription_check(call, "invited", referrer_id)


@private_router.callback_query(F.data == "subscribe_true")
async def confirm_subscription(call: types.CallbackQuery) -> None:
    await _handle_subscription_check(call, "standard", None)
