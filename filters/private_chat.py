from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message
from aiogram.enums import ChatType


class ChatPrivateFilter(BaseFilter):
    def __init__(self, chat_type: str | list[str]):
        self.chat_type = chat_type

    async def __call__(self, message: Message) -> bool:
        return message.chat.type == ChatType.PRIVATE


def private_only(router: Router) -> Router:
    """Attach private-chat filters for both messages and callback queries."""
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    return router
