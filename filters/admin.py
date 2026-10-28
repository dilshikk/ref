from aiogram.filters import BaseFilter, Filter
from aiogram.types import Message, CallbackQuery

from data.config import ADMINS, MONEY_ADMINS


class IsBotAdminFilter(BaseFilter):
    def __init__(self, user_ids: list[int]):
        self.user_ids = user_ids

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in self.user_ids


class IsBotAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in ADMINS


class IsMoneyAdmin(Filter):
    """Денежные операции (выплаты, балансы) — только MONEY_ADMINS (#23)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id in MONEY_ADMINS


class IsAdminWithoutMoneyRights(Filter):
    """Обычный админ без права на денежные операции."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if event.from_user is None:
            return False
        return event.from_user.id in ADMINS and event.from_user.id not in MONEY_ADMINS
