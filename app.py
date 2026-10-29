# /www/wwwroot/uz_ibot/app.py
import asyncio
import sys

from aiogram import Dispatcher
from loguru import logger

from data.config import ADMINS
from loader import db, bot
from middlewares.antispam import SmartRateLimiter
from middlewares.callback_answer import CallbackAutoAnswerMiddleware
from services.audit_log import audit_log_service
from services.error_notify import notify_admins_about_error
from utils.fsm_storage import PostgresStorage
from utils.payment_details import EncryptionConfigError, ensure_encryption_configured

# Состояния форм хранятся в базе: при перезапуске бота незаконченный
# вывод средств или настройка в админке больше не теряются (раньше — MemoryStorage)
fsm_storage = PostgresStorage(db)


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
    from middlewares.logger import ActionLoggerMiddleware
    from middlewares.blocked import BlockedUserMiddleware
    from middlewares.subscription import SubscriptionMiddleware
    from middlewares.text_input import TextInputGuardMiddleware
    from middlewares.group_activity import GroupActivityMiddleware

    action_logger = ActionLoggerMiddleware()
    blocked_guard = BlockedUserMiddleware()
    subscription_guard = SubscriptionMiddleware()

    # Кто пишет в группу — признак живого участника при проверке «добавил N» (#18).
    # Outer-middleware видит каждое сообщение и не забирает его у обработчиков.
    dispatcher.message.outer_middleware(GroupActivityMiddleware())

    # Блокировка проверяется первой — заблокированный пользователь не доходит до хэндлеров
    dispatcher.message.middleware(blocked_guard)
    dispatcher.callback_query.middleware(blocked_guard)

    # Без подписки на обязательные каналы бот не работает (кроме /start и «Я подписался»)
    dispatcher.message.middleware(subscription_guard)
    dispatcher.callback_query.middleware(subscription_guard)

    # Стикер/фото в форме, где нужен текст, больше не роняет обработчик
    dispatcher.message.middleware(TextInputGuardMiddleware())

    dispatcher.message.middleware(action_logger)
    dispatcher.callback_query.middleware(action_logger)


async def setup_aiogram(dispatcher: Dispatcher) -> None:
    # Фильтры объявлены рядом со своими хэндлерами, отдельная настройка не нужна
    logger.info("Configuring aiogram")
    setup_handlers(dispatcher=dispatcher)
    setup_middlewares(dispatcher=dispatcher)
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
    # Кому уже выдана награда за голос (одна на пользователя и вопрос)
    await db.create_table_poll_vote_rewards()
    # Кто кого добавил в группу вручную: один человек засчитывается один раз
    await db.create_table_group_manual_adds()
    # Вступления сверх антифрод-лимита ждут решения админа, а не теряются
    await db.create_table_referral_holds()
    # Антифрод (#18): блокировка после неверной капчи, проверка добавленных, активность в группе
    await db.create_table_withdraw_captcha_state()
    await db.create_table_pending_group_adds()
    await db.create_table_group_member_activity()
    # Состояния форм (переживают перезапуск)
    await fsm_storage.create_table()

    # Миграции
    await db.add_is_blocked_column()
    await db.add_invited_at_column()
    await db.add_invite_link_column()
    await db.add_language_column()
    await db.add_users_created_at_column()
    # Защита от двойного начисления за одного приглашённого
    await db.ensure_unique_invited_members()
    # Баланс в деньгах: смена цены за реферала не пересчитывает старые балансы
    await db.ensure_money_columns()
    # Снятие начисления, если приглашённый вышел из группы
    await db.ensure_referral_reversal_columns()
    # Реквизиты выплат: колонка TEXT и шифрование старых заявок (#20)
    await db.ensure_payout_card_encryption()
    # Рассылки: прогресс по получателям и пометка заблокировавших бота (#19)
    await db.ensure_broadcast_progress_columns()

    logger.info("✅ Все таблицы проверены и готовы.")


async def aiogram_on_startup_polling(dispatcher: Dispatcher) -> None:
    from utils.bot_commands import set_all_commands
    from utils.startup_notifications import on_startup_notify
    from services.broadcast_runner import start_broadcast_scheduler

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
    # Накопившиеся за время простоя события НЕ выбрасываем: среди них вступления
    # в группу по реферальным ссылкам — их потеря означает потерю начислений.
    await bot.delete_webhook(drop_pending_updates=False)
    await setup_aiogram(dispatcher=dispatcher)
    # Прерванные рестартом рассылки продолжатся с сохранённого места (#19)
    await start_broadcast_scheduler()
    await on_startup_notify(bot=bot)
    await set_all_commands(bot=bot, admin_ids=ADMINS)


async def aiogram_on_shutdown_polling(dispatcher: Dispatcher) -> None:
    from services.broadcast_runner import stop_broadcast_scheduler

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
        await dispatcher.storage.close()
    except Exception as e:
        logger.warning(f"Ошибка закрытия storage: {e}")

    # База закрывается последней: хранилище форм работает через неё
    try:
        await db.close_pool()
    except Exception as e:
        logger.warning(f"Ошибка закрытия пула БД: {e}")


async def send_error_to_admin(error: Exception) -> None:
    """Отправляет критическую ошибку всем администраторам."""
    if _is_network_resolution_error(error):
        logger.warning("Сеть/DNS недоступны: пропускаю отправку ошибки админам в Telegram.")
        return
    await notify_admins_about_error(bot, error, title="Критическая ошибка: бот остановлен")


async def run_polling() -> None:
    """
    Основная async точка входа.
    Вынесена отдельно, чтобы send_error_to_admin работал
    в том же event loop, что и bot — сессия ещё открыта.
    """
    dispatcher = Dispatcher(storage=fsm_storage)

    dispatcher.startup.register(aiogram_on_startup_polling)
    dispatcher.shutdown.register(aiogram_on_shutdown_polling)

    # Outer: срабатывает для любого нажатия, даже если хэндлер не найден — кнопки не висят
    dispatcher.callback_query.outer_middleware(CallbackAutoAnswerMiddleware())

    # Один общий экземпляр: сообщения и нажатия одного пользователя идут по очереди,
    # чтобы кнопка и ответ в форме не обрабатывались одновременно и не ломали состояние.
    rate_limiter = SmartRateLimiter(limit=1.0)
    dispatcher.message.middleware(rate_limiter)
    dispatcher.callback_query.middleware(rate_limiter)

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member", "poll_answer"],
            close_bot_session=False,  # сессия нужна, чтобы после падения успеть сообщить админу
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
    # До подключения к базе и Telegram: без ключа реквизиты легли бы открытым текстом (#45)
    try:
        ensure_encryption_configured()
    except EncryptionConfigError as e:
        logger.critical(f"🔐 {e}")
        sys.exit(1)

    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
