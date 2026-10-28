from aiogram.filters import BaseFilter, Filter
from aiogram.types import Message, CallbackQuery

from data.config import ADMINS


class IsBotAdminFilter(BaseFilter):
    def __init__(self, user_ids: list[int]):
        self.user_ids = user_ids

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in self.user_ids


class IsBotAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in ADMINS
