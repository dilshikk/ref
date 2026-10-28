from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
)

USER_COMMANDS = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="help", description="Помощь и список команд"),
    BotCommand(command="top10", description="Топ рефералов"),
]

ADMIN_COMMANDS = [
    BotCommand(command="admin", description="Панель администратора"),
    BotCommand(command="help", description="Помощь и список команд"),
    BotCommand(command="top10", description="Топ рефералов"),
    BotCommand(command="stats", description="Статистика бота"),
    BotCommand(command="holds", description="Рефералы на удержании"),
]


async def set_default_commands(bot: Bot) -> None:
    await set_user_commands(bot)
    await clear_group_commands(bot)


async def set_user_commands(bot: Bot) -> None:
    await bot.set_my_commands(commands=USER_COMMANDS, scope=BotCommandScopeAllPrivateChats())


async def clear_group_commands(bot: Bot) -> None:
    """Убирает меню команд в группах и супергруппах."""
    await bot.set_my_commands(commands=[], scope=BotCommandScopeAllGroupChats())


async def set_admin_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Личные команды админа — видны только ему в ЛС с ботом."""
    for admin_id in admin_ids:
        await bot.set_my_commands(
            commands=ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=admin_id),
        )


async def set_all_commands(bot: Bot, admin_ids: list[int]) -> None:
    await set_user_commands(bot)
    await clear_group_commands(bot)
    await set_admin_commands(bot, admin_ids)


def format_commands(commands: list[BotCommand]) -> str:
    return "\n".join(f"/{command.command} — {command.description}" for command in commands)