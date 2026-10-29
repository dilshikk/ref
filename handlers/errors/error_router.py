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
from services.audit_log import audit_log_service
from services.error_notify import _signature, notify_admins_about_error

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

# Действие в audit_logs: попадает в «📋 Логи» → «❌ Ошибки» (#96)
HANDLER_ERROR_ACTION = "handler_error"


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


def _actor_id(update: Update | None) -> int | None:
    """Telegram id пользователя, на действии которого случилась ошибка."""
    if update is None:
        return None
    if update.message and update.message.from_user:
        return update.message.from_user.id
    if update.callback_query:
        return update.callback_query.from_user.id
    return None


async def _record_error(exc: BaseException, update: Update | None, description: str) -> None:
    # Ошибка могла быть в самой базе — запись в журнал не должна ронять обработчик ошибок
    try:
        await audit_log_service.record_system_action(
            actor_id=_actor_id(update),
            action=HANDLER_ERROR_ACTION,
            entity_type="bot",
            entity_id=_signature(exc),
            payload={
                "error": type(exc).__name__,
                "message": str(exc)[:500],
                "context": description[:400],
            },
        )
    except Exception as e:
        logger.warning(f"Не удалось записать ошибку в audit_logs: {e}")


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
        await _record_error(exc, update, description)

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
