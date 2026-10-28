from aiogram import Bot
from loguru import logger

from data.config import ADMINS


async def on_startup_notify(bot: Bot) -> None:
    bot_info = await bot.me()
    text = (
        "<b>🚀 Бот успешно запущен!</b>\n\n"
        f"<b>🆔 ID бота:</b> {bot_info.id}\n"
        f"<b>🤖 Username:</b> @{bot_info.username}"
    )

    for admin in ADMINS:
        try:
            await bot.send_message(int(admin), text, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"Ошибка при отправке уведомления администратору {admin}: {e}")
