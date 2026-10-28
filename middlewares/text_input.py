from aiogram import BaseMiddleware
from aiogram.types import Message
from loguru import logger

from states.flows import AdminFlow, SupportFlow

# Состояния форм, в которых бот специально принимает фото, видео, документы и т.п.
MEDIA_ALLOWED_STATES: frozenset[str] = frozenset({
    SupportFlow.waiting_message.state,      # обращение в поддержку
    AdminFlow.support_reply_input.state,    # ответ поддержки
    AdminFlow.payout_receipt_input.state,   # чек выплаты
    AdminFlow.ask_ad_content.state,         # контент рассылки
})


class TextInputGuardMiddleware(BaseMiddleware):
    """
    Во всех остальных формах бот ждёт текст и делает message.text.strip().
    Стикер/фото/голосовое давали message.text = None и роняли обработчик.
    Теперь такое сообщение не доходит до обработчика: пользователь видит подсказку,
    а форма остаётся на том же шаге.
    """

    async def __call__(self, handler, event: Message, data):
        if event.chat.type != "private" or event.text is not None:
            return await handler(event, data)

        raw_state = data.get("raw_state")
        if raw_state is None or raw_state in MEDIA_ALLOWED_STATES:
            return await handler(event, data)

        logger.debug(f"Нетекстовое сообщение в текстовой форме {raw_state} от {event.from_user.id if event.from_user else '?'}")
        await event.answer("✍️ Пожалуйста, отправьте ответ текстом.")
        return None
