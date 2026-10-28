# /www/wwwroot/uz_ibot/handlers/errors/error_router.py
from typing import Any

from aiogram import Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramUnauthorizedError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramConflictError,
    TelegramForbiddenError,
    RestartingTelegram,
    CallbackAnswerException,
    TelegramEntityTooLarge,
    TelegramRetryAfter,
    TelegramMigrateToChat,
    TelegramServerError,
)
from aiogram.handlers import ErrorHandler
from loguru import logger

router = Router(name="errors.error_router")


@router.errors()
class MyErrorHandler(ErrorHandler):
    async def handle(self) -> Any:
        exc = self.event

        if isinstance(exc, TelegramUnauthorizedError):
            logger.error(f"❌ Unauthorized: недействительный токен. {self.exception_message}")
            return True

        if isinstance(exc, TelegramNetworkError):
            logger.exception(f"🌐 NetworkError: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramNotFound):
            logger.exception(f"🔍 NotFound: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramConflictError):
            logger.exception(f"⚔️ ConflictError: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramForbiddenError):
            logger.exception(f"🚫 ForbiddenError: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, CallbackAnswerException):
            logger.exception(f"🔁 CallbackAnswerException: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramMigrateToChat):
            logger.exception(f"➡️ MigrateToChat: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramServerError):
            logger.exception(f"💻 ServerError: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramRetryAfter):
            logger.exception(f"⏳ RetryAfter: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramEntityTooLarge):
            logger.exception(f"📦 EntityTooLarge: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramBadRequest):
            logger.exception(f"🧾 BadRequest: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, RestartingTelegram):
            logger.exception(f"🔄 RestartingTelegram: {self.exception_message}\nUpdate: {self.update}")
            return True

        if isinstance(exc, TelegramAPIError):
            logger.exception(f"⚠️ TelegramAPIError: {self.exception_message}\nUpdate: {self.update}")
            return True

        logger.exception(f"❗ Необработанное исключение:\nUpdate: {self.update}\n{self.exception_name}: {exc}")
        return False
