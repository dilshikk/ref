from aiogram.filters import BaseFilter, Filter
from aiogram.types import Message, CallbackQuery

from data.config import ADMINS
from data.settings import get_settings


def is_money_admin(user_id: int) -> bool:
    """Денежный админ (#23). Список хранится в bot_settings и меняется из админки (#116),
    поэтому читаем его при каждой проверке, а не один раз при импорте."""
    return user_id in get_settings().money_admins


class IsBotAdminFilter(BaseFilter):
    def __init__(self, user_ids: list[int]):
        self.user_ids = user_ids

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in self.user_ids


class IsBotAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in ADMINS


class IsMoneyAdmin(Filter):
    """Денежные операции (выплаты, балансы) — только денежные админы (#23)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and is_money_admin(event.from_user.id)


class IsAdminWithoutMoneyRights(Filter):
    """Обычный админ без права на денежные операции."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if event.from_user is None:
            return False
        return event.from_user.id in ADMINS and not is_money_admin(event.from_user.id)
