# /www/wwwroot/uz_ibot/app.py
import asyncio
import traceback

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from data.config import ADMINS
from loader import db, bot
from middlewares.antispam import SmartRateLimiter
from services.audit_log import audit_log_service


def _is_network_resolution_error(error: Exception) -> bool:
    message = str(error).lower()
    patterns = (
        "clientconnectordnserror",
        "temporary failure in name resolution",
        "cannot connect to host api.telegram.org",
    )
    return any(pattern in message for pattern in patterns)


def setup_handlers(dispatcher: Dispatcher) -> None:
    from handlers import setup_routers
    dispatcher.include_router(setup_routers())


def setup_middlewares(dispatcher: Dispatcher) -> None:
    from middlewares.throttling import ThrottlingMiddleware
    from middlewares.logger import ActionLoggerMiddleware

    action_logger = ActionLoggerMiddleware()

    dispatcher.message.middleware(ThrottlingMiddleware(slow_mode_delay=0.5))
    dispatcher.message.middleware(action_logger)
    dispatcher.callback_query.middleware(action_logger)


def setup_filters(dispatcher: Dispatcher) -> None:
    """Router-specific filters are declared next to their handlers."""
    return None


async def setup_aiogram(dispatcher: Dispatcher) -> None:
    logger.info("Configuring aiogram")
    setup_handlers(dispatcher=dispatcher)
    setup_middlewares(dispatcher=dispatcher)
    setup_filters(dispatcher=dispatcher)
    logger.info("Configured aiogram")


async def database_connected() -> None:
    """Создание всех таблиц и применение миграций при старте."""
    # Таблицы
    await db.create_table_users()
    await db.create_table_channel()
    await db.create_table_users_invite_count()
    await db.create_table_user_invite_members()
    await db.create_table_group_add_member_count()
    await db.create_table_payout_requests()
    await db.create_table_audit_logs()
    await db.create_table_user_activity()
    await db.create_table_scheduled_broadcasts()
    await db.create_table_broadcast_send_logs()
    await db.create_table_poll_instances()
    await db.create_table_poll_vote_answers()

    # Миграции
    await db.add_is_blocked_column()
    await db.add_invited_at_column()
    await db.add_invite_link_column()
    await db.add_language_column()
    await db.add_users_created_at_column()

    logger.info("✅ Все таблицы проверены и готовы.")


async def aiogram_on_startup_polling(dispatcher: Dispatcher) -> None:
    from utils.bot_commands import set_all_commands
    from utils.startup_notifications import on_startup_notify
    from handlers.private.admin_panel import start_broadcast_scheduler

    logger.info("Connecting to database")
    await db.create_pool()
    await database_connected()
    await audit_log_service.record_system_action(
        actor_id=None,
        action="bot_start",
        entity_type="bot",
        entity_id="startup",
        payload={"phase": "startup"},
    )

    logger.info("Starting polling")
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_aiogram(dispatcher=dispatcher)
    await start_broadcast_scheduler()
    await on_startup_notify(bot=bot)
    await set_all_commands(bot=bot, admin_ids=ADMINS)


async def aiogram_on_shutdown_polling(dispatcher: Dispatcher) -> None:
    from handlers.private.admin_panel import stop_broadcast_scheduler

    logger.info("Stopping polling")
    try:
        await stop_broadcast_scheduler()
    except Exception as e:
        logger.warning(f"Ошибка остановки планировщика: {e}")

    try:
        await audit_log_service.record_system_action(
            actor_id=None,
            action="bot_stop",
            entity_type="bot",
            entity_id="shutdown",
            payload={"phase": "shutdown"},
        )
    except Exception as e:
        logger.warning(f"Не удалось записать bot_stop в аудит: {e}")

    try:
        await db.close_pool()
    except Exception as e:
        logger.warning(f"Ошибка закрытия пула БД: {e}")

    try:
        await dispatcher.storage.close()
    except Exception as e:
        logger.warning(f"Ошибка закрытия storage: {e}")


async def send_error_to_admin(error: Exception) -> None:
    """Отправляет traceback ошибки всем администраторам."""
    if _is_network_resolution_error(error):
        logger.warning("Сеть/DNS недоступны: пропускаю отправку ошибки админам в Telegram.")
        return

    trace_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    error_text = (
        f"⚠️ <b>Критическая ошибка в боте!</b>\n\n"
        f"<code>{trace_text}</code>"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, error_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Не удалось отправить ошибку админу {admin_id}: {e}")


async def run_polling() -> None:
    """
    Основная async точка входа.
    Вынесена отдельно, чтобы send_error_to_admin работал
    в том же event loop, что и bot — сессия ещё открыта.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())

    dispatcher.startup.register(aiogram_on_startup_polling)
    dispatcher.shutdown.register(aiogram_on_shutdown_polling)

    dispatcher.message.middleware(SmartRateLimiter(limit=1.0))
    dispatcher.callback_query.middleware(SmartRateLimiter(limit=1.0))

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member", "poll_answer"],
            close_bot_session=True,
        )
    except Exception as e:
        logger.exception("💥 Критическая ошибка в polling!")
        try:
            await audit_log_service.record_system_action(
                actor_id=None,
                action="polling_exception",
                entity_type="bot",
                entity_id="polling",
                payload={"error": str(e)},
            )
        except Exception as log_error:
            logger.warning(f"Не удалось записать polling_exception в аудит: {log_error}")
        await send_error_to_admin(e)
    finally:
        try:
            await bot.session.close()
        except Exception as session_error:
            logger.warning(f"Ошибка закрытия bot session: {session_error}")
        logger.info("❌ Бот остановлен!")


def main() -> None:
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")


if __name__ == "__main__":
    main()