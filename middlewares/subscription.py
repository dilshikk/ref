# middlewares/subscription.py
"""Не пускает к функциям бота без подписки на обязательные каналы."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from data.config import ADMINS
from keyboards.inline_keyboards.actions import subscribe_keyboard
from services.subscription import is_user_subscribed

_SUBSCRIBE_TEXT = "Чтобы пользоваться ботом, подпишитесь на все каналы 👇"


def _is_exempt(event: TelegramObject) -> bool:
    """/start и «Я подписался» сами проверяют подписку и показывают нужные кнопки."""
    if isinstance(event, Message):
        return (event.text or "").strip().startswith("/start")
    if isinstance(event, CallbackQuery):
        return (event.data or "").startswith("subscribe_true")
    return True


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot or user.id in ADMINS or _is_exempt(event):
            return await handler(event, data)

        # Только личка: в группах и для событий групп подписка не требуется
        chat = event.chat if isinstance(event, Message) else (
            event.message.chat if isinstance(event, CallbackQuery) and event.message else None
        )
        if chat is None or chat.type != "private":
            return await handler(event, data)

        try:
            subscribed = await is_user_subscribed(user.id)
        except Exception as e:
            logger.warning(f"Проверка подписки не удалась user={user.id}: {e}")
            subscribed = True
        if subscribed:
            return await handler(event, data)

        keyboard = await subscribe_keyboard()
        if isinstance(event, CallbackQuery):
            await event.answer("❗️ Сначала подпишитесь на все каналы", show_alert=True)
            if event.message is not None:
                await event.message.answer(_SUBSCRIBE_TEXT, reply_markup=keyboard)
        elif isinstance(event, Message):
            await event.answer(_SUBSCRIBE_TEXT, reply_markup=keyboard)
        return None
