from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from loguru import logger


class CallbackAutoAnswerMiddleware(BaseMiddleware):
    """
    Гарантирует ответ на каждое нажатие inline-кнопки.

    Если хэндлер не вызвал call.answer() (или кнопка старая и хэндлера нет),
    Telegram показывает «часики» до 15 секунд. Регистрируется как outer middleware,
    поэтому срабатывает и для нажатий без подходящего хэндлера.
    Повторный ответ на уже отвеченный callback Telegram отклоняет — это игнорируется.
    """

    async def __call__(self, handler, event: CallbackQuery, data):
        try:
            return await handler(event, data)
        finally:
            try:
                await event.answer()
            except TelegramBadRequest:
                pass
            except Exception as e:
                logger.debug(f"Не удалось ответить на callback {event.id}: {e}")
