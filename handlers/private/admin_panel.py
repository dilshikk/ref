# /www/wwwroot/uz_ibot/handlers/private/admin_panel.py
import asyncio
from datetime import datetime
import json
import yaml
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from loguru import logger

from filters import private_only
from keyboards.reply_keyboards import (
    bot_kb,
    bot_menu_settings_kb,
    broadcasts_kb,
    channels_groups_kb,
    finance_kb,
    logs_kb,
    reply_keyboard_for_admin,
    stats_kb,
    security_kb,
    settings_kb,
    subscription_kb,
    users_kb,
)
from loader import db, bot as app_bot
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS, url_yaml, config_dir
from services.activity import activity_service
from services.audit_log import audit_log_service
from utils.bot_commands import ADMIN_COMMANDS, USER_COMMANDS, format_commands, set_all_commands

router = private_only(Router(name="private.admin_panel"))

_cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True,
)

SCHEDULE_DT_FORMAT = "%Y-%m-%d %H:%M"
SCHEDULE_POLL_INTERVAL_SEC = 5
_scheduler_task: asyncio.Task[Any] | None = None
LOGS_PAGE_SIZE = 7

_TARIFF_FIELDS: dict[str, dict[str, Any]] = {
    "min_balance": {"label": "Мин. вывод", "icon": "💸", "allow_zero": True},
    "max_withdraw": {"label": "Макс. вывод", "icon": "💰", "allow_zero": False},
    "invited_credit": {"label": "За приглашение", "icon": "🎁", "allow_zero": True},
    "vote_credit": {"label": "За голос", "icon": "🗳", "allow_zero": True},
}


def _save_yaml():
    """Сохраняет url_yaml в файл."""
    with open(f"{config_dir}/url.yml", "w", encoding="utf-8") as f:
        yaml.dump(url_yaml, f, sort_keys=False, allow_unicode=True)


def _tariffs_text() -> str:
    return (
        "💰 <b>Тарифы</b>\n\n"
        f"Мин. вывод: <b>{url_yaml.get('min_balance', 0)}</b>\n"
        f"Макс. вывод: <b>{url_yaml.get('max_withdraw', 0)}</b>\n"
        f"За приглашение: <b>{url_yaml.get('invited_credit', 0)}</b>\n"
        f"За голос: <b>{url_yaml.get('vote_credit', 0)}</b>\n\n"
        "Выберите параметр ниже и отправьте новое число."
    )


def _tariffs_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💸 Мин. вывод", callback_data="tariff_edit:min_balance"),
                InlineKeyboardButton(text="💰 Макс. вывод", callback_data="tariff_edit:max_withdraw"),
            ],
            [
                InlineKeyboardButton(text="🎁 За приглашение", callback_data="tariff_edit:invited_credit"),
                InlineKeyboardButton(text="🗳 За голос", callback_data="tariff_edit:vote_credit"),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="tariff_refresh")],
        ]
    )


def _tariff_input_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="tariff_edit_cancel")]]
    )


async def _render_tariffs_panel(message: types.Message) -> None:
    await message.answer(
        _tariffs_text(),
        parse_mode="HTML",
        reply_markup=_tariffs_inline_kb(),
    )


async def _get_user_totals() -> dict[str, int]:
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    total_users = len(users)
    blocked_users = sum(1 for user in users if user.get("is_blocked"))
    return {"total_users": total_users, "blocked_users": blocked_users}


async def _get_referral_totals() -> dict[str, int]:
    referrals = cast(dict[str, Any] | None, await db.execute(
        "SELECT COALESCE(SUM(history_count), 0) AS total_referrals, COALESCE(SUM(current_count), 0) AS current_referrals FROM UserInviteCount",
        fetchone=True,
    ))
    return {
        "total_referrals": int(referrals["total_referrals"] or 0) if referrals else 0,
        "current_referrals": int(referrals["current_referrals"] or 0) if referrals else 0,
    }


async def _get_channel_totals() -> dict[str, int]:
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    return {"total_channels": len(channels)}


async def _get_finance_totals() -> dict[str, int]:
    payouts = cast(list[dict[str, Any]], await db.get_pending_payouts() or [])
    counts = cast(dict[str, Any] | None, await db.execute(
        "SELECT COALESCE(SUM(current_count), 0) AS balance_units FROM UserInviteCount",
        fetchone=True,
    ))
    balance_units = int(counts["balance_units"] or 0) if counts else 0
    return {"pending_payouts": len(payouts), "balance_units": balance_units}


async def _show_main_section(message: types.Message, text: str, keyboard: ReplyKeyboardMarkup) -> None:
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_placeholder(message: types.Message, title: str, body: str, keyboard: ReplyKeyboardMarkup) -> None:
    await message.answer(f"<b>{title}</b>\n\n{body}", parse_mode="HTML", reply_markup=keyboard)


def _resolve_payment_report_target() -> int | str | None:
    raw = url_yaml.get("payment_chat_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        value = raw.strip()
        if value.startswith("-100") and value[1:].isdigit():
            return int(value)
        if value.startswith("@") and len(value) > 1:
            return value

    channel_raw = url_yaml.get("payment_channel")
    if isinstance(channel_raw, str):
        value = channel_raw.strip()
        if value.startswith("-100") and value[1:].isdigit():
            return int(value)
        if value.startswith("@") and len(value) > 1:
            return value
    return None


def _parse_payment_chat_id(value: str) -> int | str | None:
    raw = value.strip()
    if raw.startswith("-100") and raw[1:].isdigit():
        return int(raw)
    if raw.startswith("@") and len(raw) > 1 and " " not in raw:
        return raw
    return None


async def _send_payout_report(
    message: types.Message,
    payout: dict[str, Any],
    admin_id: int,
    include_receipt: bool,
) -> bool:
    bot = message.bot
    if bot is None:
        return False

    target = _resolve_payment_report_target()
    if target is None:
        return False

    amount = payout.get("amount")
    amount_text = str(amount) if amount is not None else "-"
    card_raw = str(payout.get("card_number") or "")
    card_masked = f"****{card_raw[-4:]}" if len(card_raw) >= 4 else "-"
    user_id = payout.get("user_id") or "-"
    full_name = str(payout.get("full_name") or "-")
    req_id = payout.get("id") or "-"

    report_text = (
        "💸 <b>Отчет о выплате</b>\n\n"
        f"🆔 Заявка: <code>{req_id}</code>\n"
        f"👤 Пользователь: <a href='tg://user?id={user_id}'>{full_name}</a>\n"
        f"🧾 User ID: <code>{user_id}</code>\n"
        f"💰 Сумма: <b>{amount_text}</b>\n"
        f"💳 Карта: <code>{card_masked}</code>\n"
        f"👮 Подтвердил админ: <code>{admin_id}</code>\n"
        f"🕒 Время: <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b>\n"
        f"📎 Чек: <b>{'приложен' if include_receipt else 'не приложен'}</b>"
    )

    await bot.send_message(target, report_text, parse_mode="HTML")
    if include_receipt:
        await message.copy_to(target)
    return True


async def _record_setting_change(actor_id: int, setting_name: str, old_value, new_value) -> None:
    await audit_log_service.record_admin_action(
        actor_id=actor_id,
        action="setting_changed",
        entity_type="setting",
        entity_id=setting_name,
        old_value={setting_name: old_value},
        new_value={setting_name: new_value},
        payload={"setting": setting_name},
    )


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "-")


def _short_text(value: Any, max_len: int = 120) -> str:
    text = str(value or "")
    return text if len(text) <= max_len else f"{text[:max_len - 3]}..."


def _payload_preview(value: Any, max_len: int = 140) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return _short_text(text, max_len=max_len)


def _logs_keyboard(category: str, days: int, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    prev_offset = max(0, offset - LOGS_PAGE_SIZE)
    next_offset = offset + LOGS_PAGE_SIZE
    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"logs_view:{category}:{days}:{prev_offset}"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"logs_view:{category}:{days}:{next_offset}"))

    keyboard_rows = [
        [
            InlineKeyboardButton(text="24ч", callback_data=f"logs_view:{category}:1:0"),
            InlineKeyboardButton(text="7д", callback_data=f"logs_view:{category}:7:0"),
            InlineKeyboardButton(text="30д", callback_data=f"logs_view:{category}:30:0"),
        ]
    ]
    if nav_row:
        keyboard_rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _period_title(days: int) -> str:
    if days == 1:
        return "24 часа"
    if days == 7:
        return "7 дней"
    if days == 30:
        return "30 дней"
    return f"{days} дней"


async def _fetch_logs_page(category: str, days: int, offset: int) -> tuple[str, list[str], bool]:
    limit = LOGS_PAGE_SIZE + 1

    if category == "users":
        rows = cast(list[dict[str, Any]], await db.execute(
            """
            SELECT created_at, user_id, event, chat_id, payload
            FROM user_activity
            WHERE created_at >= NOW() - make_interval(days => $1)
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            parameters=(days, limit, offset),
            fetchall=True,
        ) or [])
        lines = [
            (
                f"• <b>{_fmt_dt(r.get('created_at'))}</b> | user: <code>{r.get('user_id')}</code>\n"
                f"  event: <code>{_short_text(r.get('event'), 60)}</code>\n"
                f"  chat: <code>{r.get('chat_id') or '-'}</code>\n"
                f"  payload: <code>{_payload_preview(r.get('payload'))}</code>"
            )
            for r in rows[:LOGS_PAGE_SIZE]
        ]
        return "👥 Логи пользователей", lines, len(rows) > LOGS_PAGE_SIZE

    if category == "finance":
        rows = cast(list[dict[str, Any]], await db.execute(
            """
            SELECT created_at, actor_id, actor_type, action, entity_type, entity_id, payload
            FROM audit_logs
            WHERE created_at >= NOW() - make_interval(days => $1)
              AND (
                action ILIKE '%withdraw%'
                OR action ILIKE '%payout%'
                OR action ILIKE '%balance%'
                OR entity_type IN ('payout', 'withdrawal', 'balance')
              )
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            parameters=(days, limit, offset),
            fetchall=True,
        ) or [])
        lines = [
            (
                f"• <b>{_fmt_dt(r.get('created_at'))}</b> | actor: <code>{r.get('actor_id') or '-'}</code> ({r.get('actor_type')})\n"
                f"  action: <code>{_short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{_short_text(r.get('entity_id'), 40)}</code>\n"
                f"  payload: <code>{_payload_preview(r.get('payload'))}</code>"
            )
            for r in rows[:LOGS_PAGE_SIZE]
        ]
        return "💰 Логи финансов", lines, len(rows) > LOGS_PAGE_SIZE

    if category == "groups":
        rows = cast(list[dict[str, Any]], await db.execute(
            """
            SELECT created_at, actor_id, actor_type, action, entity_type, entity_id, payload
            FROM audit_logs
            WHERE created_at >= NOW() - make_interval(days => $1)
              AND (
                entity_type IN ('group', 'channel')
                OR action ILIKE '%group%'
                OR action ILIKE '%channel%'
              )
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            parameters=(days, limit, offset),
            fetchall=True,
        ) or [])
        lines = [
            (
                f"• <b>{_fmt_dt(r.get('created_at'))}</b> | actor: <code>{r.get('actor_id') or '-'}</code> ({r.get('actor_type')})\n"
                f"  action: <code>{_short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{_short_text(r.get('entity_id'), 40)}</code>\n"
                f"  payload: <code>{_payload_preview(r.get('payload'))}</code>"
            )
            for r in rows[:LOGS_PAGE_SIZE]
        ]
        return "📢 Логи групп", lines, len(rows) > LOGS_PAGE_SIZE

    if category == "bot":
        rows = cast(list[dict[str, Any]], await db.execute(
            """
            SELECT created_at, actor_id, actor_type, action, entity_type, entity_id, payload
            FROM audit_logs
            WHERE created_at >= NOW() - make_interval(days => $1)
              AND (
                actor_type = 'system'
                OR entity_type = 'bot'
                OR action ILIKE 'bot_%'
                OR action ILIKE 'polling_%'
              )
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            parameters=(days, limit, offset),
            fetchall=True,
        ) or [])
        lines = [
            (
                f"• <b>{_fmt_dt(r.get('created_at'))}</b> | actor: <code>{r.get('actor_id') or '-'}</code> ({r.get('actor_type')})\n"
                f"  action: <code>{_short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{_short_text(r.get('entity_id'), 40)}</code>\n"
                f"  payload: <code>{_payload_preview(r.get('payload'))}</code>"
            )
            for r in rows[:LOGS_PAGE_SIZE]
        ]
        return "🤖 События бота", lines, len(rows) > LOGS_PAGE_SIZE

    if category == "settings":
        rows = cast(list[dict[str, Any]], await db.execute(
            """
            SELECT created_at, actor_id, actor_type, action, entity_type, entity_id, payload
            FROM audit_logs
            WHERE created_at >= NOW() - make_interval(days => $1)
              AND (action = 'setting_changed' OR entity_type = 'setting')
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            parameters=(days, limit, offset),
            fetchall=True,
        ) or [])
        lines = [
            (
                f"• <b>{_fmt_dt(r.get('created_at'))}</b> | actor: <code>{r.get('actor_id') or '-'}</code> ({r.get('actor_type')})\n"
                f"  action: <code>{_short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{_short_text(r.get('entity_id'), 40)}</code>\n"
                f"  payload: <code>{_payload_preview(r.get('payload'))}</code>"
            )
            for r in rows[:LOGS_PAGE_SIZE]
        ]
        return "⚙️ Логи настроек", lines, len(rows) > LOGS_PAGE_SIZE

    # errors
    rows = cast(list[dict[str, Any]], await db.execute(
        """
        SELECT created_at, source, summary, details
        FROM (
            SELECT
                created_at,
                'audit' AS source,
                action AS summary,
                CAST(payload AS TEXT) AS details
            FROM audit_logs
            WHERE created_at >= NOW() - make_interval(days => $1)
              AND (
                action ILIKE '%error%'
                OR action ILIKE '%exception%'
                OR action ILIKE '%failed%'
                OR CAST(payload AS TEXT) ILIKE '%error%'
              )

            UNION ALL

            SELECT
                COALESCE(finished_at, created_at) AS created_at,
                'scheduled' AS source,
                CONCAT('scheduled_job#', id::TEXT) AS summary,
                COALESCE(last_error, 'failed') AS details
            FROM scheduled_broadcasts
            WHERE status = 'failed'
              AND COALESCE(finished_at, created_at) >= NOW() - make_interval(days => $1)
        ) t
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        parameters=(days, limit, offset),
        fetchall=True,
    ) or [])
    lines = [
        (
            f"• <b>{_fmt_dt(r.get('created_at'))}</b> | source: <code>{r.get('source')}</code>\n"
            f"  summary: <code>{_short_text(r.get('summary'), 70)}</code>\n"
            f"  details: <code>{_short_text(r.get('details'), 140)}</code>"
        )
        for r in rows[:LOGS_PAGE_SIZE]
    ]
    return "❌ Ошибки", lines, len(rows) > LOGS_PAGE_SIZE


async def _open_logs_view(
    message: types.Message,
    category: str,
    days: int = 1,
    offset: int = 0,
    edit_message: Any | None = None,
) -> None:
    title, lines, has_next = await _fetch_logs_page(category, days, offset)
    period = _period_title(days)
    page = offset // LOGS_PAGE_SIZE + 1

    text = (
        f"<b>{title}</b>\n"
        f"Период: <b>{period}</b>\n"
        f"Страница: <b>{page}</b>\n\n"
    )
    if not lines:
        text += "Записей пока нет."
    else:
        text += "\n\n".join(lines)

    kb = _logs_keyboard(category=category, days=days, offset=offset, has_next=has_next)
    if edit_message is not None:
        await cast(Any, edit_message).edit_text(text, parse_mode="HTML", reply_markup=kb)
        return
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _scheduled_status_text(status: str) -> str:
    return {
        "pending": "⏳ Ожидает",
        "running": "🚀 Выполняется",
        "done": "✅ Завершена",
        "failed": "❌ Ошибка",
        "canceled": "🛑 Отменена",
    }.get(status, status)


def _make_broadcast_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    button_text = str(data.get("button_text") or "Открыть")
    url = str(data.get("url") or "https://t.me")
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]],
    )


async def _run_broadcast(data: dict[str, Any], sender_bot) -> tuple[int, int, int]:
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    total = len(users)
    success = 0
    failed = 0
    keyboard = _make_broadcast_keyboard(data)
    media_type = str(data.get("media_type") or "")
    file_id = str(data.get("file_id") or "")
    caption = str(data.get("caption") or "")

    for user in users:
        user_id = int(user.get("telegram_id") or 0)
        if user_id <= 0:
            failed += 1
            continue
        try:
            if media_type == "photo":
                await sender_bot.send_photo(chat_id=user_id, photo=file_id, caption=caption, reply_markup=keyboard)
            elif media_type == "video":
                await sender_bot.send_video(chat_id=user_id, video=file_id, caption=caption, reply_markup=keyboard)
            elif media_type == "audio":
                await sender_bot.send_audio(chat_id=user_id, audio=file_id, caption=caption, reply_markup=keyboard)
            elif media_type == "document":
                await sender_bot.send_document(chat_id=user_id, document=file_id, caption=caption, reply_markup=keyboard)
            success += 1
        except Exception as e:
            logger.warning(f"Ошибка рассылки {user_id}: {e}")
            failed += 1
        await asyncio.sleep(0.05)

    return total, success, failed


def _parse_payload(payload_raw: Any) -> dict[str, Any]:
    if isinstance(payload_raw, dict):
        return payload_raw
    if isinstance(payload_raw, str):
        try:
            decoded = json.loads(payload_raw)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            return {}
    return {}


async def _process_scheduled_job(job: dict[str, Any]) -> None:
    job_id = int(job.get("id") or 0)
    actor_id = int(job.get("actor_id") or 0)
    payload = _parse_payload(job.get("payload"))
    scheduled_for = str(job.get("scheduled_for") or "")
    total = 0
    success = 0
    failed = 0

    try:
        await audit_log_service.record_admin_action(
            actor_id=actor_id,
            action="broadcast_scheduled_started",
            entity_type="broadcast",
            entity_id=str(job_id),
            payload={"scheduled_for": scheduled_for},
        )

        total, success, failed = await _run_broadcast(payload, app_bot)

        await db.mark_scheduled_broadcast_done(
            job_id=job_id,
            total_users=total,
            success_count=success,
            failed_count=failed,
        )

        await audit_log_service.record_admin_action(
            actor_id=actor_id,
            action="broadcast_scheduled_finished",
            entity_type="broadcast",
            entity_id=str(job_id),
            payload={"total_users": total, "success": success, "failed": failed},
        )
        await db.insert_broadcast_send_log(
            source="scheduled",
            actor_id=actor_id,
            scheduled_job_id=job_id,
            entity_id=str(job_id),
            status="done",
            total_users=total,
            success_count=success,
            failed_count=failed,
            payload={"scheduled_for": scheduled_for, "media_type": payload.get("media_type")},
        )
    except Exception as e:
        logger.exception(f"Ошибка запланированной рассылки {job_id}: {e}")
        await db.mark_scheduled_broadcast_failed(job_id=job_id, error_text=str(e))
        await db.insert_broadcast_send_log(
            source="scheduled",
            actor_id=actor_id,
            scheduled_job_id=job_id,
            entity_id=str(job_id),
            status="failed",
            total_users=total,
            success_count=success,
            failed_count=failed,
            error_text=str(e),
            payload={"scheduled_for": scheduled_for, "media_type": payload.get("media_type")},
        )


async def _scheduled_broadcast_loop() -> None:
    while True:
        try:
            due_jobs = cast(list[dict[str, Any]], await db.claim_due_scheduled_broadcasts(limit=5) or [])
            for job in due_jobs:
                await _process_scheduled_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Ошибка цикла планировщика рассылок: {e}")

        await asyncio.sleep(SCHEDULE_POLL_INTERVAL_SEC)


async def start_broadcast_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return

    # Recover jobs that were marked running during an unexpected shutdown.
    await db.requeue_running_scheduled_broadcasts()
    _scheduler_task = asyncio.create_task(_scheduled_broadcast_loop(), name="scheduled-broadcast-loop")
    logger.info("✅ Планировщик рассылок запущен")


async def stop_broadcast_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return

    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    finally:
        _scheduler_task = None
    logger.info("🛑 Планировщик рассылок остановлен")


# ==========================
# 🔙 УНИВЕРСАЛЬНАЯ ОТМЕНА
# ==========================

@router.message(F.text == "🔙 Назад", IsBotAdminFilter(ADMINS))
async def go_back_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


@router.message(F.text == "⬅️ Назад", IsBotAdminFilter(ADMINS))
async def go_back_admin_alt(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


# ==========================
# 🔐 АДМИН-ПАНЕЛЬ
# ==========================

@router.message(Command("admin"), IsBotAdminFilter(ADMINS))
async def admin_panel(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в <b>админ-панель</b>!",
        parse_mode="HTML",
        reply_markup=reply_keyboard_for_admin(),
    )


@router.message(F.text == "📊 Статистика", IsBotAdminFilter(ADMINS))
async def stats_menu(message: types.Message):
    users = await _get_user_totals()
    channels = await _get_channel_totals()
    referrals = await _get_referral_totals()
    payouts = await _get_finance_totals()
    activity = await activity_service.dashboard()
    await _show_main_section(
        message,
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users['total_users']}</b>\n"
        f"🚫 Заблокированных: <b>{users['blocked_users']}</b>\n"
        f"📢 Каналов: <b>{channels['total_channels']}</b>\n"
        f"🎁 Всего рефералов: <b>{referrals['total_referrals']}</b>\n"
        f"💸 Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n\n"
        f"Сегодня DAU: <b>{activity['dau']}</b>\n"
        f"WAU: <b>{activity['wau']}</b>\n"
        f"MAU: <b>{activity['mau']}</b>\n"
        f"Retention: <b>{activity['retention']}%</b>\n"
        f"Новые пользователи сегодня: <b>{activity['new_today']}</b>\n"
        f"Вернувшиеся сегодня: <b>{activity['returning_today']}</b>",
        stats_kb(),
    )


@router.message(F.text == "💰 Финансы", IsBotAdminFilter(ADMINS))
async def finance_menu(message: types.Message):
    payouts = await _get_finance_totals()
    payment_target = _resolve_payment_report_target()
    payment_target_text = str(payment_target) if payment_target is not None else "не задан"
    await message.answer(
        "💰 <b>Финансы</b>\n\n"
        f"💸 Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n"
        f"⭐ Условных единиц баланса: <b>{payouts['balance_units']}</b>\n"
        f"🆔 Чат отчётов выплат: <code>{payment_target_text}</code>\n\n"
        "Здесь собраны денежные настройки и отчёты.",
        parse_mode="HTML",
        reply_markup=finance_kb(),
    )


@router.message(F.text == "👥 Пользователи", IsBotAdminFilter(ADMINS))
async def users_menu(message: types.Message):
    await message.answer(
        "👥 <b>Пользователи</b>\n\n"
        "Откройте список или найдите конкретного пользователя.",
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "📢 Каналы и группы", IsBotAdminFilter(ADMINS))
async def channels_groups_menu(message: types.Message):
    channels = await _get_channel_totals()
    await message.answer(
        "📢 <b>Каналы и группы</b>\n\n"
        f"📋 Подключено каналов: <b>{channels['total_channels']}</b>\n\n"
        "Управление обязательными подписками и групповыми параметрами.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "🤖 Бот", IsBotAdminFilter(ADMINS))
async def bot_menu(message: types.Message):
    await message.answer(
        "🤖 <b>Бот</b>\n\n"
        "Команды и служебные настройки бота.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "📨 Рассылки", IsBotAdminFilter(ADMINS))
async def broadcasts_menu(message: types.Message):
    await message.answer(
        "📨 <b>Рассылки</b>\n\n"
        "Создайте новую рассылку или вернитесь в главное меню.",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


@router.message(F.text == "⚙️ Настройки", IsBotAdminFilter(ADMINS))
async def settings_menu(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "Здесь собраны меню-команды и базовые параметры бота.",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "🔒 Безопасность", IsBotAdminFilter(ADMINS))
async def security_menu(message: types.Message):
    await message.answer(
        "🔒 <b>Безопасность</b>\n\n"
        "Антифрод и защитные лимиты.",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "📋 Логи", IsBotAdminFilter(ADMINS))
async def logs_menu(message: types.Message):
    await message.answer(
        "📋 <b>Логи</b>\n\n"
        "В этой версии логирование доступно в консоли сервера и через middleware."
        " Если нужен просмотр логов внутри бота, это лучше вынести в отдельное хранилище.",
        parse_mode="HTML",
        reply_markup=logs_kb(),
    )


@router.message(F.text == "👥 Новые пользователи", IsBotAdminFilter(ADMINS))
async def stats_new_users(message: types.Message):
    users = await _get_user_totals()
    new_1d = await db.count_new_users_since(1)
    new_7d = await db.count_new_users_since(7)
    new_30d = await db.count_new_users_since(30)

    yesterday_row = cast(dict[str, Any] | None, await db.execute(
        """
        SELECT COUNT(*) AS count
        FROM Users
        WHERE created_at >= NOW() - INTERVAL '48 hours'
          AND created_at < NOW() - INTERVAL '24 hours'
        """,
        fetchone=True,
    ))
    yesterday = int(yesterday_row["count"] or 0) if yesterday_row else 0
    trend = new_1d - yesterday
    if trend > 0:
        trend_text = f"📈 +{trend} к вчера"
    elif trend < 0:
        trend_text = f"📉 {trend} к вчера"
    else:
        trend_text = "➖ Без изменений к вчера"

    await message.answer(
        "👥 <b>Новые пользователи</b>\n\n"
        f"Всего зарегистрировано: <b>{users['total_users']}</b>\n"
        f"За 24ч: <b>{new_1d}</b>\n"
        f"За 7д: <b>{new_7d}</b>\n"
        f"За 30д: <b>{new_30d}</b>\n\n"
        f"{trend_text}",
        parse_mode="HTML",
        reply_markup=stats_kb(),
    )


@router.message(F.text == "📈 Активность", IsBotAdminFilter(ADMINS))
async def stats_activity(message: types.Message):
    activity = await activity_service.dashboard()
    events_1d = await db.count_activity_events_since(1)
    events_7d = await db.count_activity_events_since(7)
    await message.answer(
        "📈 <b>Активность</b>\n\n"
        f"DAU (24ч): <b>{activity['dau']}</b>\n"
        f"WAU (7д): <b>{activity['wau']}</b>\n"
        f"MAU (30д): <b>{activity['mau']}</b>\n"
        f"Retention: <b>{activity['retention']}%</b>\n"
        f"Новые за 24ч: <b>{activity['new_today']}</b>\n"
        f"Вернувшиеся за 24ч: <b>{activity['returning_today']}</b>\n\n"
        f"События за 24ч: <b>{events_1d}</b>\n"
        f"События за 7д: <b>{events_7d}</b>",
        parse_mode="HTML",
        reply_markup=stats_kb(),
    )


@router.message(F.text == "📢 Каналы", IsBotAdminFilter(ADMINS))
async def stats_channels(message: types.Message):
    channels = await _get_channel_totals()
    await _show_placeholder(
        message,
        "📢 Каналы",
        f"Всего подключено: <b>{channels['total_channels']}</b>.",
        stats_kb(),
    )


@router.message(F.text == "💳 Финансы", IsBotAdminFilter(ADMINS))
async def stats_finance(message: types.Message):
    payouts = await _get_finance_totals()
    await _show_placeholder(
        message,
        "💳 Финансы",
        f"Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n"
        f"Балансовых единиц в системе: <b>{payouts['balance_units']}</b>.",
        stats_kb(),
    )


@router.message(F.text == "🎁 Реферальная статистика", IsBotAdminFilter(ADMINS))
async def stats_referrals(message: types.Message):
    referrals = await _get_referral_totals()
    await _show_placeholder(
        message,
        "🎁 Реферальная статистика",
        f"Всего приглашений: <b>{referrals['total_referrals']}</b>\n"
        f"Текущий активный счётчик: <b>{referrals['current_referrals']}</b>.",
        stats_kb(),
    )


@router.message(F.text.in_({"📅 Сегодня", "📆 Неделя", "🗓 Месяц", "🌍 За всё время"}), IsBotAdminFilter(ADMINS))
async def stats_timeframes(message: types.Message):
    period_map: dict[str, int | None] = {
        "📅 Сегодня": 1,
        "📆 Неделя": 7,
        "🗓 Месяц": 30,
        "🌍 За всё время": None,
    }
    title = message.text or "Статистика"
    days = period_map.get(title)

    if days is None:
        users_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM Users",
            fetchone=True,
        ))
        active_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(DISTINCT user_id) AS count FROM user_activity",
            fetchone=True,
        ))
        activity_events_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM user_activity",
            fetchone=True,
        ))
        audit_events_row = cast(dict[str, Any] | None, await db.execute(
            "SELECT COUNT(*) AS count FROM audit_logs",
            fetchone=True,
        ))

        total_users = int(users_row["count"] or 0) if users_row else 0
        active_users = int(active_row["count"] or 0) if active_row else 0
        activity_events = int(activity_events_row["count"] or 0) if activity_events_row else 0
        audit_events = int(audit_events_row["count"] or 0) if audit_events_row else 0

        text = (
            f"<b>{title}</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users}</b>\n"
            f"🟢 Активные пользователи: <b>{active_users}</b>\n"
            f"📌 События активности: <b>{activity_events}</b>\n"
            f"🧾 Аудит-события: <b>{audit_events}</b>"
        )
    else:
        active_users = await db.count_active_users_since(days)
        new_users = await db.count_new_users_since(days)
        returning_users = await db.count_returning_users_since(days)
        activity_events = await db.count_activity_events_since(days)
        audit_events = await db.count_audit_logs_since(days)

        text = (
            f"<b>{title}</b>\n\n"
            f"🟢 Активные пользователи: <b>{active_users}</b>\n"
            f"🆕 Новые пользователи: <b>{new_users}</b>\n"
            f"🔁 Вернувшиеся пользователи: <b>{returning_users}</b>\n"
            f"📌 События активности: <b>{activity_events}</b>\n"
            f"🧾 Аудит-события: <b>{audit_events}</b>"
        )

    await message.answer(text, parse_mode="HTML", reply_markup=stats_kb())


@router.message(F.text == "📋 Показать логи", IsBotAdminFilter(ADMINS))
async def logs_placeholder(message: types.Message):
    activity_today = await db.count_activity_events_since(1)
    audit_today = await db.count_audit_logs_since(1)
    await message.answer(
        "📋 <b>Логи</b>\n\n"
        f"Событий активности за 24ч: <b>{activity_today}</b>\n"
        f"Аудит-записей за 24ч: <b>{audit_today}</b>\n\n"
        "Выберите категорию ниже. Для каждой категории доступны фильтры 24ч/7д/30д и пагинация.",
        parse_mode="HTML",
        reply_markup=logs_kb(),
    )


@router.message(F.text == "📋 Все пользователи", IsBotAdminFilter(ADMINS))
async def users_list_alias(message: types.Message) -> None:
    from .user_management import users_list_start

    await users_list_start(message)


@router.message(F.text == "🚫 Заблокированные пользователи", IsBotAdminFilter(ADMINS))
async def blocked_users_menu(message: types.Message):
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    blocked = [user for user in users if user.get("is_blocked")]
    text = (
        f"🚫 <b>Заблокированные пользователи</b>\n\nВсего: <b>{len(blocked)}</b>"
        if blocked
        else "🚫 <b>Заблокированные пользователи</b>\n\nСейчас заблокированных пользователей нет."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=users_kb())


@router.message(F.text == "👑 Администраторы", IsBotAdminFilter(ADMINS))
async def admins_menu(message: types.Message):
    admin_lines = "\n".join(f"• <code>{admin}</code>" for admin in ADMINS)
    await message.answer(
        "👑 <b>Администраторы</b>\n\n" + admin_lines,
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "🎁 Рефералы", IsBotAdminFilter(ADMINS))
async def referrals_menu(message: types.Message):
    top_referrals = cast(list[dict[str, Any]], await db.get_top_referrals(limit=10) or [])
    if not top_referrals:
        await message.answer("🎁 Рефералов пока нет.", reply_markup=users_kb())
        return
    lines = [
        f"{index}. <b>{row['full_name']}</b> — <b>{row['history_count']}</b>"
        for index, row in enumerate(top_referrals, start=1)
    ]
    await message.answer(
        "🎁 <b>Рефералы</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "📋 Все", IsBotAdminFilter(ADMINS))
async def channels_list_menu(message: types.Message):
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    if not channels:
        await message.answer("📋 Каналы и группы пока не добавлены.", reply_markup=channels_groups_kb())
        return
    lines = [f"• <code>{channel['chat_id']}</code> — {channel['link']}" for channel in channels]
    await message.answer(
        "📋 <b>Все каналы и группы</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "➕ Новые", IsBotAdminFilter(ADMINS))
async def channels_new_menu(message: types.Message):
    await message.answer(
        "➕ <b>Добавление нового канала или группы</b>\n\n"
        "Используйте текущее меню подписок для добавления chat_id и ссылки.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "🔍 Поиск канала", IsBotAdminFilter(ADMINS))
async def channels_search_menu(message: types.Message):
    await message.answer(
        "🔍 <b>Поиск</b>\n\n"
        "Для каналов и групп пока доступен ручной поиск по chat_id через список и настройки.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "👑 Владельцы", IsBotAdminFilter(ADMINS))
async def channels_owners_menu(message: types.Message):
    await message.answer(
        "👑 <b>Владельцы</b>\n\n"
        "Информацию о владельцах можно добавить отдельным полем в таблицу каналов.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "⚙️ Настройки каналов", IsBotAdminFilter(ADMINS))
async def channels_settings_menu(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки каналов и групп</b>\n\n"
        "Откройте меню подписок или настройки группы для изменения параметров.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "📈 Статистика", IsBotAdminFilter(ADMINS))
async def channels_statistics_menu(message: types.Message):
    channels = await _get_channel_totals()
    await message.answer(
        "📈 <b>Статистика каналов и групп</b>\n\n"
        f"Всего подключено: <b>{channels['total_channels']}</b>",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "🚫 Заблокированные каналы", IsBotAdminFilter(ADMINS))
async def channels_blocked_menu(message: types.Message):
    await message.answer(
        "🚫 <b>Заблокированные каналы и группы</b>\n\n"
        "Этот раздел можно использовать для ручного blacklist-реестра, если он появится в базе.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "🏠 Главное меню", IsBotAdminFilter(ADMINS))
async def bot_main_menu(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню бота</b>\n\n"
        "Здесь можно управлять текстами, языками, уведомлениями и реферальной логикой.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "📝 Тексты", IsBotAdminFilter(ADMINS))
async def bot_texts_menu(message: types.Message, state: FSMContext):
    templates = cast(dict[str, Any], url_yaml.get("text_templates") or {})
    start_tpl = _short_text(templates.get("start"), 90) if templates.get("start") else "—"
    help_tpl = _short_text(templates.get("help"), 90) if templates.get("help") else "—"
    support_tpl = _short_text(templates.get("support"), 90) if templates.get("support") else "—"

    await message.answer(
        "📝 <b>Тексты</b>\n\n"
        "Редактор шаблонов уже доступен.\n"
        "Отправьте строку в формате: <code>ключ=текст</code>\n\n"
        "Доступные ключи:\n"
        "• <code>start</code>\n"
        "• <code>help</code>\n"
        "• <code>support</code>\n\n"
        "Текущие значения:\n"
        f"• start: <code>{start_tpl}</code>\n"
        f"• help: <code>{help_tpl}</code>\n"
        f"• support: <code>{support_tpl}</code>\n\n"
        "Пример:\n"
        "<code>support=📞 Для связи: @your_support</code>",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_text_template)


@router.message(AdminFlow.set_text_template, IsBotAdminFilter(ADMINS))
async def update_text_template(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    raw = (message.text or "").strip()
    if "=" not in raw:
        await message.answer(
            "❌ Неверный формат.\n"
            "Используйте: <code>ключ=текст</code>\n"
            "Например: <code>start=Добро пожаловать!</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb,
        )
        return

    key_raw, value_raw = raw.split("=", 1)
    key = key_raw.strip().lower()
    value = value_raw.strip()

    if key not in {"start", "help", "support"}:
        await message.answer(
            "❌ Неизвестный ключ.\n"
            "Доступно: <code>start</code>, <code>help</code>, <code>support</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb,
        )
        return
    if not value:
        await message.answer("❌ Текст не должен быть пустым.", reply_markup=_cancel_kb)
        return

    templates = cast(dict[str, Any], url_yaml.get("text_templates") or {})
    old_value = templates.get(key)
    templates[key] = value
    url_yaml["text_templates"] = templates
    _save_yaml()

    await _record_setting_change(
        actor_id=user.id,
        setting_name=f"text_templates.{key}",
        old_value=old_value,
        new_value=value,
    )

    await message.answer(
        f"✅ Шаблон <code>{key}</code> обновлён.\n\n"
        f"Новое значение:\n<code>{_short_text(value, 300)}</code>",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )
    await state.clear()


@router.message(F.text == "🌍 Языки", IsBotAdminFilter(ADMINS))
async def bot_languages_menu(message: types.Message):
    await message.answer(
        "🌍 <b>Языки</b>\n\n"
        "В таблице пользователей уже есть поле language, но админ-управление им ещё не вынесено.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "🎁 Реферальные настройки", IsBotAdminFilter(ADMINS))
async def bot_referral_menu(message: types.Message):
    await message.answer(
        "🎁 <b>Реферальные настройки</b>\n\n"
        f"Сумма за приглашение: <b>{url_yaml.get('invited_credit', 0)}</b>\n"
        f"Антифрод-лимит: <b>{url_yaml.get('antispam_referral_limit', 100)}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "📢 Уведомления", IsBotAdminFilter(ADMINS))
async def bot_notifications_menu(message: types.Message):
    await message.answer(
        "📢 <b>Уведомления</b>\n\n"
        "Сейчас уведомления администраторам отправляются при старте бота.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "🗳 Опросы", IsBotAdminFilter(ADMINS))
async def bot_polls_menu(message: types.Message, state: FSMContext):
    default_question = "Насколько вам удобен наш бот?"
    default_options = ["Отлично", "Нормально", "Нужно улучшить"]

    question = str(url_yaml.get("poll_question") or default_question).strip()
    raw_options = url_yaml.get("poll_options")
    if isinstance(raw_options, list):
        options = [str(x).strip() for x in raw_options if str(x).strip()]
    elif isinstance(raw_options, str):
        options = [part.strip() for part in raw_options.split("|") if part.strip()]
    else:
        options = []
    if len(options) < 2:
        options = default_options
    options = options[:10]

    voters = await db.count_poll_voters(question)
    polls_sent = await db.count_poll_instances(question)
    option_rows = cast(list[dict[str, Any]], await db.get_poll_option_stats(question) or [])
    counts = {int(row.get("option_index") or 0): int(row.get("votes") or 0) for row in option_rows}

    lines = [
        f"{idx + 1}) {opt} — <b>{counts.get(idx, 0)}</b>"
        for idx, opt in enumerate(options)
    ]

    await message.answer(
        "🗳 <b>Опросы</b>\n\n"
        f"Текущий вопрос:\n<b>{question}</b>\n\n"
        "Варианты и голоса:\n"
        + "\n".join(lines)
        + "\n\n"
        f"Уникальных голосовавших: <b>{voters}</b>\n"
        f"Опрос отправлен пользователям: <b>{polls_sent}</b> раз\n\n"
        "Чтобы изменить опрос, отправьте одной строкой:\n"
        "<code>Вопрос | Вариант 1 | Вариант 2 | Вариант 3</code>",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_poll_config)


@router.message(AdminFlow.set_poll_config, IsBotAdminFilter(ADMINS))
async def update_poll_config(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    raw = (message.text or "").strip()
    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if len(parts) < 3:
        await message.answer(
            "❌ Неверный формат.\n"
            "Нужно минимум 1 вопрос и 2 варианта:\n"
            "<code>Вопрос | Вариант 1 | Вариант 2</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb,
        )
        return

    question = parts[0]
    options = parts[1:11]
    if len(options) < 2:
        await message.answer("❌ Нужно минимум 2 варианта ответа.", reply_markup=_cancel_kb)
        return

    old_question = url_yaml.get("poll_question")
    old_options = url_yaml.get("poll_options")

    url_yaml["poll_question"] = question
    url_yaml["poll_options"] = options
    _save_yaml()

    await _record_setting_change(
        actor_id=user.id,
        setting_name="poll_config",
        old_value={"poll_question": old_question, "poll_options": old_options},
        new_value={"poll_question": question, "poll_options": options},
    )

    await message.answer(
        "✅ Опрос обновлен.\n\n"
        f"Вопрос: <b>{question}</b>\n"
        "Варианты:\n"
        + "\n".join([f"{idx + 1}) {opt}" for idx, opt in enumerate(options)]),
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )
    await state.clear()


@router.message(F.text == "➕ Новая", IsBotAdminFilter(ADMINS))
async def broadcast_new_menu(message: types.Message, state: FSMContext):
    await start_ad_creation(message, state)


@router.message(F.text == "📋 История", IsBotAdminFilter(ADMINS))
async def broadcast_history_menu(message: types.Message):
    rows = cast(list[dict[str, Any]], await db.execute(
        """
        SELECT *
        FROM (
            SELECT
                created_at AS happened_at,
                'manual'::text AS source,
                actor_id,
                entity_id,
                NULL::text AS status,
                COALESCE((payload->>'total_users')::int, 0) AS total_users,
                COALESCE((payload->>'success')::int, 0) AS success_count,
                COALESCE((payload->>'failed')::int, 0) AS failed_count
            FROM audit_logs
            WHERE action = 'broadcast_finished'

            UNION ALL

            SELECT
                COALESCE(finished_at, started_at, created_at) AS happened_at,
                'scheduled'::text AS source,
                actor_id,
                id::text AS entity_id,
                status,
                COALESCE(total_users, 0) AS total_users,
                COALESCE(success_count, 0) AS success_count,
                COALESCE(failed_count, 0) AS failed_count
            FROM scheduled_broadcasts
            WHERE status IN ('done', 'failed', 'canceled')
        ) t
        ORDER BY happened_at DESC
        LIMIT $1
        """,
        parameters=(25,),
        fetchall=True,
    ) or [])

    if not rows:
        await message.answer(
            "📋 <b>История рассылок</b>\n\nПока нет завершенных рассылок.",
            parse_mode="HTML",
            reply_markup=broadcasts_kb(),
        )
        return

    lines: list[str] = []
    for row in rows:
        source = str(row.get("source") or "-")
        actor_id = row.get("actor_id") or "-"
        entity_id = row.get("entity_id") or "-"
        total_users = int(row.get("total_users") or 0)
        success_count = int(row.get("success_count") or 0)
        failed_count = int(row.get("failed_count") or 0)
        happened_at = _fmt_dt(row.get("happened_at"))
        status = str(row.get("status") or "")
        status_text = _scheduled_status_text(status) if source == "scheduled" else "✅ Завершена"
        source_text = "Планировщик" if source == "scheduled" else "Ручная"

        lines.append(
            f"• <b>{happened_at}</b> | {source_text}\n"
            f"  id: <code>{entity_id}</code> | admin: <code>{actor_id}</code>\n"
            f"  статус: <b>{status_text}</b>\n"
            f"  итог: <b>{success_count}/{total_users}</b> (ошибок: <b>{failed_count}</b>)"
        )

    await message.answer(
        "📋 <b>История рассылок</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


@router.message(F.text == "⏳ Запланированные", IsBotAdminFilter(ADMINS))
async def broadcast_scheduled_menu(message: types.Message):
    jobs = cast(list[dict[str, Any]], await db.list_scheduled_broadcasts(limit=20) or [])
    if not jobs:
        await message.answer(
            "⏳ <b>Запланированные рассылки</b>\n\nПока нет активных задач.",
            parse_mode="HTML",
            reply_markup=broadcasts_kb(),
        )
        return

    await message.answer(
        "⏳ <b>Запланированные рассылки</b>\n"
        f"Всего задач: <b>{len(jobs)}</b>\n\n"
        "Формат времени: YYYY-MM-DD HH:MM\n"
        "Задачи хранятся в базе и переживают перезапуск бота.",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )

    for job in jobs:
        job_id = int(job.get("id") or 0)
        status = _scheduled_status_text(str(job.get("status") or ""))
        scheduled_dt = cast(datetime | None, job.get("scheduled_for"))
        scheduled_for = scheduled_dt.strftime(SCHEDULE_DT_FORMAT) if scheduled_dt else "-"
        body = (
            f"🆔 <code>{job_id}</code>\n"
            f"🕒 {scheduled_for}\n"
            f"Статус: <b>{status}</b>"
        )
        if job.get("status") == "done":
            body += (
                "\n"
                f"📊 Итог: {int(job.get('success_count') or 0)}/{int(job.get('total_users') or 0)}"
                f" (ошибок: {int(job.get('failed_count') or 0)})"
            )

        markup = None
        if job.get("status") == "pending":
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="🛑 Отменить", callback_data=f"cancel_scheduled_{job_id}")
                ]]
            )

        await message.answer(body, parse_mode="HTML", reply_markup=markup)


@router.message(F.text == "📊 Статистика рассылок", IsBotAdminFilter(ADMINS))
async def broadcast_stats_menu(message: types.Message):
    m_1d = await db.get_broadcast_metrics(1)
    m_7d = await db.get_broadcast_metrics(7)
    m_30d = await db.get_broadcast_metrics(30)
    m_all = await db.get_broadcast_metrics(None)

    success_rate_7d = round((m_7d["success_count"] / m_7d["total_users"] * 100), 2) if m_7d["total_users"] else 0.0

    await message.answer(
        "📊 <b>Статистика рассылок</b>\n\n"
        "<b>За 24ч</b>\n"
        f"Запусков: <b>{m_1d['total_runs']}</b> (ручных: {m_1d['manual_runs']}, плановых: {m_1d['scheduled_runs']})\n"
        f"Отправок: <b>{m_1d['success_count']}/{m_1d['total_users']}</b> | ошибок: <b>{m_1d['failed_count']}</b>\n\n"
        "<b>За 7д</b>\n"
        f"Запусков: <b>{m_7d['total_runs']}</b> | success rate: <b>{success_rate_7d}%</b>\n"
        f"Статусы: ✅ {m_7d['done_runs']} | ❌ {m_7d['failed_runs']} | 🛑 {m_7d['canceled_runs']}\n\n"
        "<b>За 30д</b>\n"
        f"Запусков: <b>{m_30d['total_runs']}</b>\n"
        f"Отправок: <b>{m_30d['success_count']}/{m_30d['total_users']}</b> | ошибок: <b>{m_30d['failed_count']}</b>\n\n"
        "<b>За всё время</b>\n"
        f"Запусков: <b>{m_all['total_runs']}</b> (ручных: {m_all['manual_runs']}, плановых: {m_all['scheduled_runs']})\n"
        f"Отправок: <b>{m_all['success_count']}/{m_all['total_users']}</b> | ошибок: <b>{m_all['failed_count']}</b>",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


@router.message(F.text == "🔐 Безопасность", IsBotAdminFilter(ADMINS))
async def settings_security_menu(message: types.Message):
    await message.answer(
        "🔐 <b>Безопасность</b>\n\n"
        "Переход к антиспаму, банам и ограничениям.",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "🌍 Локализация", IsBotAdminFilter(ADMINS))
async def settings_localization_menu(message: types.Message):
    await message.answer(
        "🌍 <b>Локализация</b>\n\n"
        "Поддержка языков уже есть на уровне модели пользователя, но экран управления ещё можно расширить.",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "💰 Тарифы", IsBotAdminFilter(ADMINS))
async def settings_tariffs_menu(message: types.Message):
    await _render_tariffs_panel(message)


@router.callback_query(F.data == "tariff_refresh", IsBotAdminFilter(ADMINS))
async def tariff_refresh(call: types.CallbackQuery):
    if call.message is None:
        return
    await cast(Any, call.message).edit_text(
        _tariffs_text(),
        parse_mode="HTML",
        reply_markup=_tariffs_inline_kb(),
    )
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("tariff_edit:"), IsBotAdminFilter(ADMINS))
async def tariff_edit_callback(call: types.CallbackQuery, state: FSMContext):
    if call.message is None or not call.data:
        return

    key = call.data.split(":", 1)[1]
    field = _TARIFF_FIELDS.get(key)
    if field is None:
        await call.answer("Неизвестный параметр", show_alert=False)
        return

    await state.update_data(
        tariff_key=key,
        tariff_panel_chat_id=call.message.chat.id,
        tariff_panel_message_id=call.message.message_id,
    )
    await state.set_state(AdminFlow.set_tariff_inline_value)

    await cast(Any, call.message).answer(
        f"Введите новое значение для «{field['label']}».\n"
        f"Сейчас: <b>{url_yaml.get(key, 0)}</b>",
        parse_mode="HTML",
        reply_markup=_tariff_input_cancel_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "tariff_edit_cancel", IsBotAdminFilter(ADMINS))
async def tariff_edit_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.message is not None:
        await cast(Any, call.message).edit_reply_markup(reply_markup=None)
    await call.answer("Редактирование отменено")


@router.message(AdminFlow.set_tariff_inline_value, IsBotAdminFilter(ADMINS))
async def tariff_inline_value_input(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    data = await state.get_data()
    key = str(data.get("tariff_key") or "")
    field = _TARIFF_FIELDS.get(key)
    if field is None:
        await message.answer("⚠️ Сессия редактирования истекла. Откройте «💰 Тарифы» снова.", reply_markup=settings_kb())
        await state.clear()
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Введите только целое число.", reply_markup=_cancel_kb)
        return

    value = int(raw)
    if not bool(field.get("allow_zero", True)) and value <= 0:
        await message.answer("❌ Значение должно быть больше нуля.", reply_markup=_cancel_kb)
        return

    old_value = url_yaml.get(key, 0)
    url_yaml[key] = value
    _save_yaml()
    await _record_setting_change(user.id, key, old_value, value)

    panel_chat_id = data.get("tariff_panel_chat_id")
    panel_message_id = data.get("tariff_panel_message_id")
    bot = message.bot
    if bot is not None and isinstance(panel_chat_id, int) and isinstance(panel_message_id, int):
        try:
            await bot.edit_message_text(
                chat_id=panel_chat_id,
                message_id=panel_message_id,
                text=_tariffs_text(),
                parse_mode="HTML",
                reply_markup=_tariffs_inline_kb(),
            )
        except Exception:
            await _render_tariffs_panel(message)

    await message.answer(
        f"✅ {field['icon']} {field['label']} обновлен: <b>{value}</b>",
        parse_mode="HTML",
    )
    await state.clear()


@router.message(F.text == "📊 Лимиты", IsBotAdminFilter(ADMINS))
async def settings_limits_menu(message: types.Message):
    await message.answer(
        "📊 <b>Лимиты</b>\n\n"
        f"Антифрод: <b>{url_yaml.get('antispam_referral_limit', 100)}</b>\n"
        f"Участников для вывода: <b>{url_yaml.get('group_member_count', 3)}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "🤖 Поведение бота", IsBotAdminFilter(ADMINS))
async def settings_behavior_menu(message: types.Message):
    await message.answer(
        "🤖 <b>Поведение бота</b>\n\n"
        "Сейчас маршруты разделены на private/group, а приватный доступ централизован фильтром.",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "🚫 Бан пользователей", IsBotAdminFilter(ADMINS))
async def security_ban_users(message: types.Message):
    users = await _get_user_totals()
    await message.answer(
        "🚫 <b>Бан пользователей</b>\n\n"
        f"Заблокировано: <b>{users['blocked_users']}</b>",
        parse_mode="HTML",
        reply_markup=security_kb(),
    )


@router.message(F.text == "🚫 Бан групп", IsBotAdminFilter(ADMINS))
async def security_ban_groups(message: types.Message):
    await _show_placeholder(
        message,
        "🚫 Бан групп",
        "Механизм блокировки групп можно добавить отдельной таблицей blacklist.",
        security_kb(),
    )


@router.message(F.text == "🛡 Антиспам", IsBotAdminFilter(ADMINS))
async def security_antispam(message: types.Message):
    await edit_antispam_limit(message)


@router.message(F.text == "📱 Ограничения", IsBotAdminFilter(ADMINS))
async def security_limits(message: types.Message):
    await _show_placeholder(
        message,
        "📱 Ограничения",
        f"Антифрод лимит: <b>{url_yaml.get('antispam_referral_limit', 100)}</b>\n"
        f"Порог участников для вывода: <b>{url_yaml.get('group_member_count', 3)}</b>",
        security_kb(),
    )


@router.message(F.text == "📋 Белый список", IsBotAdminFilter(ADMINS))
async def security_whitelist(message: types.Message):
    await _show_placeholder(
        message,
        "📋 Белый список",
        "Белый список можно подключить через отдельную таблицу разрешённых ID.",
        security_kb(),
    )


@router.message(F.text == "👥 Пользовательские", IsBotAdminFilter(ADMINS))
async def logs_users(message: types.Message):
    await _open_logs_view(message, category="users", days=1, offset=0)


@router.message(F.text == "💰 Финансовые", IsBotAdminFilter(ADMINS))
async def logs_finance(message: types.Message):
    await _open_logs_view(message, category="finance", days=1, offset=0)


@router.message(F.text == "📢 Группы", IsBotAdminFilter(ADMINS))
async def logs_groups(message: types.Message):
    await _open_logs_view(message, category="groups", days=1, offset=0)


@router.message(F.text == "🤖 События бота", IsBotAdminFilter(ADMINS))
async def logs_bot(message: types.Message):
    await _open_logs_view(message, category="bot", days=1, offset=0)


@router.message(F.text == "⚙️ Изменения", IsBotAdminFilter(ADMINS))
async def logs_settings(message: types.Message):
    await _open_logs_view(message, category="settings", days=1, offset=0)


@router.message(F.text == "❌ Ошибки", IsBotAdminFilter(ADMINS))
async def logs_errors(message: types.Message):
    await _open_logs_view(message, category="errors", days=1, offset=0)


@router.callback_query(F.data.startswith("logs_view:"), IsBotAdminFilter(ADMINS))
async def logs_view_callback(call: types.CallbackQuery):
    if not call.data or call.message is None:
        return

    parts = call.data.split(":")
    if len(parts) != 4:
        await call.answer("Неверный формат запроса", show_alert=False)
        return

    _, category, days_raw, offset_raw = parts
    valid_categories = {"users", "finance", "groups", "bot", "settings", "errors"}
    if category not in valid_categories:
        await call.answer("Неизвестная категория", show_alert=False)
        return
    if not days_raw.isdigit() or not offset_raw.isdigit():
        await call.answer("Неверные параметры", show_alert=False)
        return

    days = int(days_raw)
    if days not in {1, 7, 30}:
        days = 1
    offset = max(0, int(offset_raw))

    await _open_logs_view(
        message=cast(Any, call.message),
        category=category,
        days=days,
        offset=offset,
        edit_message=call.message,
    )
    await call.answer()


@router.message(F.text.in_({"⚙ Меню бота", "⚙️ Меню бота"}), IsBotAdminFilter(ADMINS))
async def bot_menu_settings(message: types.Message):
    await message.answer(
        "⚙ <b>Меню бота</b>\n\n"
        "Здесь можно посмотреть и обновить команды для пользователей и админов.",
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "👥 Команды пользователей", IsBotAdminFilter(ADMINS))
async def show_user_commands(message: types.Message):
    await message.answer(
        "👥 <b>Команды пользователей</b>\n\n" + format_commands(USER_COMMANDS),
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "🛡 Команды админов", IsBotAdminFilter(ADMINS))
async def show_admin_commands(message: types.Message):
    await message.answer(
        "🛡 <b>Команды админов</b>\n\n" + format_commands(ADMIN_COMMANDS),
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "🔄 Обновить меню", IsBotAdminFilter(ADMINS))
async def refresh_bot_menu(message: types.Message):
    bot = message.bot
    if bot is None:
        await message.answer("⚠️ Не удалось обновить меню: bot недоступен.")
        return
    await set_all_commands(bot)
    await message.answer(
        "✅ <b>Меню бота обновлено</b>\n\n"
        "Команды пользователей и админов синхронизированы.",
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


# ==========================
# 📊 СТАТИСТИКА
# ==========================

# ==========================
# 📌 ПОДПИСКИ
# ==========================

@router.message(F.text == "📌 Подписки", IsBotAdminFilter(ADMINS))
async def force_subscription(message: types.Message):
    await message.answer(
        "📋 Раздел управления <b>обязательными подписками</b>:",
        parse_mode="HTML",
        reply_markup=subscription_kb(),
    )


# ==========================
# 💸 МИН. СУММА ВЫВОДА
# ==========================

@router.message(F.text == "💸 Мин. сумма вывода", IsBotAdminFilter(ADMINS))
async def edit_min_balance(message: types.Message):
    value = url_yaml.get("min_balance", 0)
    await message.answer(
        f"💸 Текущая минимальная сумма вывода: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("min_balance"),
    )


@router.callback_query(F.data == "yes_min_balance", IsBotAdminFilter(ADMINS))
async def confirm_min_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую минимальную сумму.\nСейчас: {url_yaml.get('min_balance', 0)}",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_min_balance)


@router.message(AdminFlow.set_min_balance, IsBotAdminFilter(ADMINS))
async def update_min_balance(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>10000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("min_balance", 0)
    url_yaml["min_balance"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "min_balance", old_value, url_yaml["min_balance"])
    await message.answer(
        f"✅ Минимальная сумма вывода изменена на <b>{url_yaml['min_balance']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 💰 МАКС. СУММА ВЫВОДА
# ==========================

@router.message(F.text == "💰 Макс. сумма вывода", IsBotAdminFilter(ADMINS))
async def edit_max_withdraw(message: types.Message):
    value = url_yaml.get("max_withdraw", 0)
    await message.answer(
        f"💰 Текущая максимальная сумма вывода: <b>{value}</b> сум.\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("max_withdraw"),
    )


@router.callback_query(F.data == "yes_max_withdraw", IsBotAdminFilter(ADMINS))
async def confirm_max_withdraw(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую максимальную сумму.\nСейчас: {url_yaml.get('max_withdraw', 0)}\n"
        "Пример: <code>500000</code>",
        parse_mode="HTML", reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_max_withdraw)


@router.message(AdminFlow.set_max_withdraw, IsBotAdminFilter(ADMINS))
async def update_max_withdraw(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>500000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("max_withdraw", 0)
    url_yaml["max_withdraw"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "max_withdraw", old_value, url_yaml["max_withdraw"])
    await message.answer(
        f"✅ Максимальная сумма вывода изменена на <b>{url_yaml['max_withdraw']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 🎁 СУММА ЗА ПРИГЛАШЕНИЕ
# ==========================

@router.message(F.text == "🎁 Сумма за приглашение", IsBotAdminFilter(ADMINS))
async def edit_invite_reward(message: types.Message):
    value = url_yaml.get("invited_credit", 0)
    await message.answer(
        f"👥 Текущая сумма за приглашённого: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("invite_reward"),
    )


@router.callback_query(F.data == "yes_invite_reward", IsBotAdminFilter(ADMINS))
async def confirm_invite_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую сумму за приглашение.\nСейчас: {url_yaml.get('invited_credit', 0)}",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_invite_reward)


@router.message(AdminFlow.set_invite_reward, IsBotAdminFilter(ADMINS))
async def update_invite_reward(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>5000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("invited_credit", 0)
    url_yaml["invited_credit"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "invited_credit", old_value, url_yaml["invited_credit"])
    await message.answer(
        f"✅ Сумма за приглашение изменена на <b>{url_yaml['invited_credit']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 🗳 СУММА ЗА ГОЛОС
# ==========================

@router.message(F.text == "🗳 Сумма за голос", IsBotAdminFilter(ADMINS))
async def edit_vote_price(message: types.Message):
    value = url_yaml.get("vote_credit", 0)
    await message.answer(
        f"🗳 Текущая сумма за голос: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("vote_reward"),
    )


@router.callback_query(F.data == "yes_vote_reward", IsBotAdminFilter(ADMINS))
async def confirm_vote_price(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую сумму за голос.\nСейчас: {url_yaml.get('vote_credit', 0)}",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_vote_reward)


@router.message(AdminFlow.set_vote_reward, IsBotAdminFilter(ADMINS))
async def update_vote_reward(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>200</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("vote_credit", 0)
    url_yaml["vote_credit"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "vote_credit", old_value, url_yaml["vote_credit"])
    await message.answer(
        f"✅ Сумма за голос изменена на <b>{url_yaml['vote_credit']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 📢 КАНАЛ ВЫПЛАТ
# ==========================

@router.message(F.text == "🆔 Чат выплат", IsBotAdminFilter(ADMINS))
async def edit_payment_chat_id(message: types.Message):
    value = url_yaml.get("payment_chat_id", "не задан")
    await message.answer(
        "🆔 Текущий payment_chat_id (для отчётов о выплатах):\n"
        f"<code>{value}</code>\n\n"
        "Поддерживается: <code>@channel_username</code> или <code>-1001234567890</code>.\n"
        "Хотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("payment_chat_id"),
    )


@router.callback_query(F.data == "yes_payment_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_payment_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        "Введите новый payment_chat_id.\n"
        f"Сейчас: {url_yaml.get('payment_chat_id', 'не задан')}\n"
        "Примеры: <code>@my_channel</code> или <code>-1001234567890</code>",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_payment_chat_id)


@router.message(AdminFlow.set_payment_chat_id, IsBotAdminFilter(ADMINS))
async def update_payment_chat_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    parsed = _parse_payment_chat_id(text)
    if parsed is None:
        await message.answer(
            "❌ Неверный формат!\n"
            "Используйте <code>@channel_username</code> или <code>-1001234567890</code>.",
            parse_mode="HTML",
            reply_markup=_cancel_kb,
        )
        return

    old_value = url_yaml.get("payment_chat_id", "не задан")
    url_yaml["payment_chat_id"] = parsed
    _save_yaml()
    await _record_setting_change(user.id, "payment_chat_id", old_value, url_yaml["payment_chat_id"])
    await message.answer(
        f"✅ payment_chat_id обновлён: <code>{url_yaml['payment_chat_id']}</code>",
        parse_mode="HTML",
        reply_markup=finance_kb(),
    )
    await state.clear()


# ==========================
# 🔗 ССЫЛКА ГРУППЫ
# ==========================

@router.message(F.text == "🔗 Ссылка группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_channel(message: types.Message):
    value = url_yaml.get("subcr_channel", "не задана")
    await message.answer(
        f"🔗 Текущая ссылка группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("subcr_channel"),
    )


@router.callback_query(F.data == "yes_subcr_channel", IsBotAdminFilter(ADMINS))
async def confirm_subcr_channel(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую ссылку группы.\nСейчас: {url_yaml.get('subcr_channel', 'не задана')}\n"
        "Пример: <code>https://t.me/+XXXXXXXXXX</code>",
        parse_mode="HTML", reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_subcr_channel)


@router.message(AdminFlow.set_subcr_channel, IsBotAdminFilter(ADMINS))
async def update_subcr_channel(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("https://t.me/"):
        await message.answer(
            "❌ Неверный формат!\nПример: <code>https://t.me/+XXXXXXXXXX</code>",
            parse_mode="HTML",
        )
        return
    old_value = url_yaml.get("subcr_channel", "не задана")
    url_yaml["subcr_channel"] = text
    _save_yaml()
    await _record_setting_change(user.id, "subcr_channel", old_value, url_yaml["subcr_channel"])
    await message.answer(
        f"✅ Ссылка группы обновлена!\n🔗 <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 🆔 ID ГРУППЫ
# ==========================

@router.message(F.text == "🆔 ID группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_chat_id(message: types.Message):
    value = url_yaml.get("subcr_chat_id", "не задан")
    await message.answer(
        f"🆔 Текущий ID группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("subcr_chat_id"),
    )


@router.callback_query(F.data == "yes_subcr_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_subcr_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый chat_id группы.\nСейчас: {url_yaml.get('subcr_chat_id', 'не задан')}\n"
        "Пример: <code>-1001234567890</code>",
        parse_mode="HTML", reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_subcr_chat_id)


@router.message(AdminFlow.set_subcr_chat_id, IsBotAdminFilter(ADMINS))
async def update_subcr_chat_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not (text.startswith("-100") and text[1:].isdigit()):
        await message.answer(
            "❌ Неверный формат!\nПример: <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return
    old_value = url_yaml.get("subcr_chat_id", "не задан")
    url_yaml["subcr_chat_id"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "subcr_chat_id", old_value, url_yaml["subcr_chat_id"])
    await message.answer(
        f"✅ ID группы обновлён: <code>{text}</code>",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# 👤 УЧАСТНИКОВ ДЛЯ ВЫВОДА
# ==========================

@router.message(F.text == "👤 Участников для вывода", IsBotAdminFilter(ADMINS))
async def edit_group_member_count(message: types.Message):
    value = url_yaml.get("group_member_count", 3)
    await message.answer(
        f"👥 Текущее кол-во участников для вывода: <b>{value}</b>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("group_member_count"),
    )


@router.callback_query(F.data == "yes_group_member_count", IsBotAdminFilter(ADMINS))
async def confirm_group_member_count(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новое кол-во участников.\nСейчас: {url_yaml.get('group_member_count', 3)}\n"
        "Пример: <code>5</code>",
        parse_mode="HTML", reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_group_member_count)


@router.message(AdminFlow.set_group_member_count, IsBotAdminFilter(ADMINS))
async def update_group_member_count(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>5</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("group_member_count", 3)
    url_yaml["group_member_count"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "group_member_count", old_value, url_yaml["group_member_count"])
    await message.answer(
        f"✅ Кол-во участников для вывода изменено на <b>{url_yaml['group_member_count']}</b>.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# ✏️ URL (голосование)
# ==========================

@router.message(F.text == "✏️ Изменить ссылку (URL)", IsBotAdminFilter(ADMINS))
async def edit_url(message: types.Message):
    value = url_yaml.get("url", "не задан")
    await message.answer(
        f"🌐 Текущий URL:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("url"),
    )


@router.callback_query(F.data == "yes_url", IsBotAdminFilter(ADMINS))
async def confirm_url_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый URL.\nСейчас: {url_yaml.get('url', 'не задан')}",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_url)


@router.message(AdminFlow.set_url, IsBotAdminFilter(ADMINS))
async def update_url(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("http"):
        await message.answer("❌ Введите корректную ссылку (начиная с http или https):", parse_mode="HTML")
        return
    old_value = url_yaml.get("url", "")
    url_yaml["url"] = text
    _save_yaml()
    await _record_setting_change(user.id, "url", old_value, url_yaml["url"])
    await message.answer(
        f"✅ URL обновлён: <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


# ==========================
# ❌ ОТМЕНА (inline "Нет")
# ==========================

@router.callback_query(F.data == "no", IsBotAdminFilter(ADMINS))
async def cancel_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    msg_any = cast(Any, msg)
    try:
        await msg_any.delete()
    except Exception:
        pass
    await msg_any.answer("❌ Действие отменено.", reply_markup=reply_keyboard_for_admin())
    await state.clear()


# ==========================
# 📢 РАССЫЛКА
# ==========================

@router.message(F.text.in_({"📤 Рассылка", "📤 Создать рассылку"}), IsBotAdminFilter(ADMINS))
async def start_ad_creation(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    await audit_log_service.record_admin_action(
        actor_id=user.id,
        action="broadcast_draft_started",
        entity_type="broadcast",
        entity_id=f"draft:{user.id}",
        payload={"step": "content"},
    )
    await message.answer(
        "📸 Отправьте медиафайл (фото, видео, аудио или документ):",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.ask_ad_content)


@router.message(AdminFlow.ask_ad_content, IsBotAdminFilter(ADMINS))
async def handle_media_upload(message: types.Message, state: FSMContext):
    if not (message.photo or message.video or message.audio or message.document):
        await message.answer("⚠️ Это не медиа. Отправьте фото, видео, аудио или файл.")
        return

    file_id = ""
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.audio:
        file_id = message.audio.file_id
    elif message.document:
        file_id = message.document.file_id
    media_type = (
        "photo" if message.photo else
        "video" if message.video else
        "audio" if message.audio else
        "document"
    )
    await state.update_data(media_type=media_type, file_id=file_id)
    await message.answer("✏️ Теперь отправьте текст (caption):")
    await state.set_state(AdminFlow.ask_ad_caption)


@router.message(AdminFlow.ask_ad_caption, IsBotAdminFilter(ADMINS))
async def handle_caption_input(message: types.Message, state: FSMContext):
    await state.update_data(caption=message.text)
    await message.answer("🔗 Отправьте ссылку (например: https://t.me/example):")
    await state.set_state(AdminFlow.ask_ad_url)


@router.message(AdminFlow.ask_ad_url, IsBotAdminFilter(ADMINS))
async def handle_url_input(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("⚠️ Введите корректную ссылку (начинающуюся с http или https):")
        return
    await state.update_data(url=url)
    await message.answer("✏️ Введите текст для кнопки:")
    await state.set_state(AdminFlow.ask_ad_button_text)


@router.message(AdminFlow.ask_ad_button_text, IsBotAdminFilter(ADMINS))
async def handle_button_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    button_text = (message.text or "").strip()
    if not button_text:
        await message.answer("⚠️ Текст кнопки не должен быть пустым.")
        return
    url = str(data.get("url") or "")
    caption = str(data.get("caption") or "")
    file_id = str(data.get("file_id") or "")
    media_type = str(data.get("media_type") or "")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]]
    )
    await state.update_data(button_text=button_text)
    await message.answer("👁 Предпросмотр вашего поста:", reply_markup=reply_keyboard_for_admin())

    if media_type == "photo":
        await message.answer_photo(photo=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "video":
        await message.answer_video(video=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "audio":
        await message.answer_audio(audio=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "document":
        await message.answer_document(document=file_id, caption=caption, reply_markup=keyboard)

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send_ad"),
            InlineKeyboardButton(text="⏳ Запланировать", callback_data="plan_send_ad"),
        ], [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_send_ad"),
        ]]
    )
    await message.answer("Отправить эту рассылку всем пользователям?", reply_markup=confirm_keyboard)
    await state.set_state(AdminFlow.are_you_sure)


@router.callback_query(F.data == "plan_send_ad", IsBotAdminFilter(ADMINS))
async def plan_send_ad(call: types.CallbackQuery, state: FSMContext):
    if call.message is None:
        return
    await cast(Any, call.message).answer(
        "Введите дату и время отправки в формате:\n"
        "<code>YYYY-MM-DD HH:MM</code>\n"
        "Пример: <code>2026-07-23 18:30</code>",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.ask_ad_schedule_at)


@router.message(AdminFlow.ask_ad_schedule_at, IsBotAdminFilter(ADMINS))
async def schedule_broadcast_input(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    raw = (message.text or "").strip()
    try:
        scheduled_for = datetime.strptime(raw, SCHEDULE_DT_FORMAT)
    except ValueError:
        await message.answer(
            "❌ Неверный формат времени.\n"
            "Используйте: <code>YYYY-MM-DD HH:MM</code>",
            parse_mode="HTML",
        )
        return

    if scheduled_for <= datetime.now():
        await message.answer("❌ Время должно быть в будущем.")
        return

    data = await state.get_data()
    payload = {
        "media_type": str(data.get("media_type") or ""),
        "file_id": str(data.get("file_id") or ""),
        "caption": str(data.get("caption") or ""),
        "url": str(data.get("url") or ""),
        "button_text": str(data.get("button_text") or ""),
    }
    created = cast(dict[str, Any] | None, await db.create_scheduled_broadcast(
        actor_id=user.id,
        scheduled_for=scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
        payload_json=json.dumps(payload, ensure_ascii=False),
    ))
    if not created:
        await message.answer("❌ Не удалось создать задачу рассылки.", reply_markup=broadcasts_kb())
        return
    job_id = int(created.get("id") or 0)

    await audit_log_service.record_admin_action(
        actor_id=user.id,
        action="broadcast_scheduled_created",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={"scheduled_for": scheduled_for.strftime(SCHEDULE_DT_FORMAT)},
    )

    await message.answer(
        "✅ Рассылка запланирована.\n"
        f"🆔 <code>{job_id}</code>\n"
        f"🕒 {scheduled_for.strftime(SCHEDULE_DT_FORMAT)}",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )
    await state.clear()


@router.callback_query(F.data == "confirm_send_ad", IsBotAdminFilter(ADMINS))
async def confirm_send_ad(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    total = len(users)
    success = 0
    failed = 0

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=data["button_text"], url=data["url"])]]
    )
    if call.message is None or call.bot is None:
        return
    msg_any = cast(Any, call.message)
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="broadcast_started",
        entity_type="broadcast",
        entity_id=f"send:{call.from_user.id}:{call.id}",
        payload={"total_users": total, "media_type": data.get("media_type")},
    )
    progress_msg = await msg_any.edit_text(
        f"🚀 Рассылка началась...\n\n📤 Отправлено: <b>0 / {total}</b>",
        parse_mode="HTML",
    )

    for index, user in enumerate(users, start=1):
        user_id = int(user.get("telegram_id") or 0)
        if user_id <= 0:
            failed += 1
            continue
        try:
            if data["media_type"] == "photo":
                await call.bot.send_photo(chat_id=user_id, photo=data["file_id"], caption=data["caption"], reply_markup=keyboard)
            elif data["media_type"] == "video":
                await call.bot.send_video(chat_id=user_id, video=data["file_id"], caption=data["caption"], reply_markup=keyboard)
            elif data["media_type"] == "audio":
                await call.bot.send_audio(chat_id=user_id, audio=data["file_id"], caption=data["caption"], reply_markup=keyboard)
            elif data["media_type"] == "document":
                await call.bot.send_document(chat_id=user_id, document=data["file_id"], caption=data["caption"], reply_markup=keyboard)
            success += 1
        except Exception as e:
            logger.warning(f"Ошибка рассылки {user_id}: {e}")
            failed += 1

        if index % 20 == 0 or index == total:
            await progress_msg.edit_text(
                f"📦 Рассылка идёт...\n"
                f"✅ Успешно: <b>{success}</b>\n"
                f"⚠️ Ошибок: <b>{failed}</b>\n"
                f"📊 Прогресс: <b>{index}/{total}</b>",
                parse_mode="HTML",
            )
        await asyncio.sleep(0.05)

    await progress_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"👥 Всего: <b>{total}</b>\n"
        f"✅ Успешно: <b>{success}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>",
        parse_mode="HTML",
    )
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="broadcast_finished",
        entity_type="broadcast",
        entity_id=f"send:{call.from_user.id}:{call.id}",
        payload={"total_users": total, "success": success, "failed": failed},
    )
    await db.insert_broadcast_send_log(
        source="manual",
        actor_id=call.from_user.id,
        scheduled_job_id=None,
        entity_id=f"send:{call.from_user.id}:{call.id}",
        status="done",
        total_users=total,
        success_count=success,
        failed_count=failed,
        payload={"media_type": data.get("media_type"), "button_text": data.get("button_text")},
    )
    await state.clear()


@router.callback_query(F.data == "cancel_send_ad", IsBotAdminFilter(ADMINS))
async def cancel_send_ad(call: types.CallbackQuery, state: FSMContext):
    if call.message is not None:
        await cast(Any, call.message).edit_text("❌ Рассылка отменена.")
    await state.clear()


@router.callback_query(F.data.startswith("cancel_scheduled_"), IsBotAdminFilter(ADMINS))
async def cancel_scheduled_broadcast(call: types.CallbackQuery):
    if not call.data:
        return
    job_id_str = call.data.replace("cancel_scheduled_", "", 1)
    if not job_id_str.isdigit():
        return
    job_id = int(job_id_str)

    canceled = cast(dict[str, Any] | None, await db.cancel_scheduled_broadcast(job_id))
    if not canceled:
        if call.message is not None:
            await cast(Any, call.message).answer("⚠️ Задача уже выполнена, отменена или отсутствует.")
        return

    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="broadcast_scheduled_canceled",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={"scheduled_for": str(canceled.get("scheduled_for") or "")},
    )
    await db.insert_broadcast_send_log(
        source="scheduled",
        actor_id=call.from_user.id,
        scheduled_job_id=job_id,
        entity_id=str(job_id),
        status="canceled",
        total_users=0,
        success_count=0,
        failed_count=0,
        payload={"scheduled_for": str(canceled.get("scheduled_for") or "")},
    )

    if call.message is not None:
        await cast(Any, call.message).edit_reply_markup(reply_markup=None)
        await cast(Any, call.message).answer("🛑 Запланированная рассылка отменена.")

# ==========================
# 🚨 АНТИФРОД ЛИМИТ
# ==========================

@router.message(F.text == "🚨 Антифрод лимит", IsBotAdminFilter(ADMINS))
async def edit_antispam_limit(message: types.Message):
    value = url_yaml.get("antispam_referral_limit", 100)
    await message.answer(
        f"🚨 Текущий лимит рефералов в час: <b>{value}</b>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("antispam_limit"),
    )


@router.callback_query(F.data == "yes_antispam_limit", IsBotAdminFilter(ADMINS))
async def confirm_antispam_limit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новый лимит рефералов в час.\nСейчас: {url_yaml.get('antispam_referral_limit', 100)}\n"
        "Пример: <code>50</code>",
        parse_mode="HTML", reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.set_antispam_limit)


@router.message(AdminFlow.set_antispam_limit, IsBotAdminFilter(ADMINS))
async def update_antispam_limit(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>50</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("antispam_referral_limit", 100)
    url_yaml["antispam_referral_limit"] = int(text)
    _save_yaml()
    await _record_setting_change(user.id, "antispam_referral_limit", old_value, url_yaml["antispam_referral_limit"])
    await message.answer(
        f"✅ Лимит антифрода изменён на <b>{url_yaml['antispam_referral_limit']}</b> рефералов/час.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()
