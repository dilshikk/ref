# /www/wwwroot/uz_ibot/handlers/errors/error_router.py
from typing import Any

from aiogram import Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.handlers import ErrorHandler
from aiogram.types import Update
from loguru import logger

from data.config import ADMINS
from services.error_notify import notify_admins_about_error

router = Router(name="errors.error_router")

# Обычные ситуации (пользователь заблокировал бота, «message is not modified» и т.п.)
# — только в лог, чтобы не заспамить админа.
_LOG_ONLY = (
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramRetryAfter,
    TelegramNetworkError,
    TelegramServerError,
)


def _describe(update: Update | None) -> tuple[str, int | None]:
    """Короткое описание события для админа и чат пользователя (если это личка)."""
    if update is None:
        return "Событие неизвестно", None
    if update.message:
        m = update.message
        who = m.from_user.id if m.from_user else "?"
        what = m.text or m.caption or f"[{m.content_type}]"
        chat = m.chat.id if m.chat.type == "private" else None
        return f"Пользователь {who}, сообщение: {what}", chat
    if update.callback_query:
        c = update.callback_query
        chat = c.message.chat.id if c.message and c.message.chat.type == "private" else None
        return f"Пользователь {c.from_user.id}, кнопка: {c.data}", chat
    if update.chat_member:
        return f"Событие участника в чате {update.chat_member.chat.id}", None
    if update.poll_answer:
        return "Ответ в опросе", None
    return f"Событие {update.event_type}", None


@router.errors()
class MyErrorHandler(ErrorHandler):
    async def handle(self) -> Any:
        # В aiogram 3 сюда приходит ErrorEvent(update, exception). Раньше код проверял
        # isinstance на самом ErrorEvent, поэтому ни одна ветка не срабатывала.
        exc: BaseException = getattr(self.event, "exception", self.event)
        update: Update | None = getattr(self.event, "update", None) or self.data.get("event_update")
        description, user_chat_id = _describe(update)

        if isinstance(exc, TelegramUnauthorizedError):
            # Токен недействителен — отправить сообщение всё равно нельзя
            logger.error(f"❌ Unauthorized: недействительный токен. {exc}")
            return True

        if isinstance(exc, _LOG_ONLY):
            logger.warning(f"{type(exc).__name__}: {exc} | {description}")
            return True

        logger.opt(exception=exc).error(f"❗ Ошибка в обработчике | {description}")

        level = "Ошибка Telegram API" if isinstance(exc, TelegramAPIError) else "Ошибка в боте"
        await notify_admins_about_error(self.bot, exc, title=level, context=description)

        # Пользователь не должен сидеть в тишине
        if user_chat_id is not None and user_chat_id not in ADMINS:
            try:
                await self.bot.send_message(
                    user_chat_id,
                    "⚠️ Что-то пошло не так. Мы уже знаем об этом. Попробуйте ещё раз чуть позже.",
                )
            except Exception as e:
                logger.debug(f"Не удалось сообщить пользователю {user_chat_id} об ошибке: {e}")
        return True
