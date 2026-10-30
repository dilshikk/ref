from aiogram import Bot
from loguru import logger

from data.settings import get_settings
from loader import db
from services.settings_store import GROUP_NOT_SET_TEXT


class GroupNotConfiguredError(RuntimeError):
    """ID группы ещё не задан в админке (#116) — ссылку создать некуда."""


async def ensure_invite_link(bot: Bot, user_id: int) -> str:
    """
    Возвращает персональную инвайт-ссылку пользователя.
    Если ссылки нет — создаёт через Telegram API и сохраняет в БД.
    Если уже есть — возвращает существующую (без лишнего API-запроса).
    """
    existing = await db.get_user_invite_link(user_id)
    if existing:
        return existing

    return await _create_and_save_link(bot, user_id)


async def _create_and_save_link(bot: Bot, user_id: int) -> str:
    """
    Создаёт новую инвайт-ссылку через Telegram API и сохраняет в БД.
    Бросает исключение наверх — хэндлер сам решает что показать пользователю.
    """
    # Группа берётся из bot_settings при каждом вызове: смена в админке действует сразу
    group_id = get_settings().group_id
    if group_id is None:
        logger.error(f"Нельзя создать ссылку для user={user_id}: {GROUP_NOT_SET_TEXT}")
        raise GroupNotConfiguredError(GROUP_NOT_SET_TEXT)

    invite = await bot.create_chat_invite_link(
        chat_id=group_id,
        name=f"ref_{user_id}",
        creates_join_request=False,
    )

    await db.save_user_invite_link(user_id, invite.invite_link)
    logger.info(f"Создана инвайт-ссылка для user={user_id}: {invite.invite_link}")

    return invite.invite_link
