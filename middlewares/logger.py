# middlewares/logger.py

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from typing import Callable, Awaitable, Any
from data.config import LOG_GROUP_ID
from services.activity import activity_service


class ActionLoggerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data["bot"]

        try:
            if isinstance(event, Message) and event.text:
                user = event.from_user
                await activity_service.record(
                    user_id=user.id,
                    event="message_received",
                    chat_id=event.chat.id,
                    session_id=str(event.message_id),
                    payload={"text": event.text[:500]},
                )
                await bot.send_message(
                    LOG_GROUP_ID,
                    f"💬 <b>Сообщение</b>\n"
                    f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"📝 {event.text}",
                    parse_mode="HTML",
                )

            elif isinstance(event, CallbackQuery):
                user = event.from_user
                chat_id = event.message.chat.id if event.message else None
                await activity_service.record(
                    user_id=user.id,
                    event="callback_query",
                    chat_id=chat_id,
                    session_id=event.id,
                    payload={"data": event.data},
                )
                await bot.send_message(
                    LOG_GROUP_ID,
                    f"🔘 <b>Кнопка</b>\n"
                    f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"📌 {event.data}",
                    parse_mode="HTML",
                )
        except Exception:
            pass

        return await handler(event, data)
