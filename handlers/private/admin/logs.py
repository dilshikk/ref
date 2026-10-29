# handlers/private/admin/logs.py
"""📋 Логи: просмотр журналов по категориям и периодам."""
import json
from datetime import datetime
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from filters import private_only
from keyboards.reply_keyboards import logs_kb
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS

from .common import short_text

router = private_only(Router(name="private.admin.logs"))


LOGS_PAGE_SIZE = 7


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "-")


def _payload_preview(value: Any, max_len: int = 140) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return short_text(text, max_len=max_len)


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
                f"  event: <code>{short_text(r.get('event'), 60)}</code>\n"
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
                f"  action: <code>{short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{short_text(r.get('entity_id'), 40)}</code>\n"
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
                f"  action: <code>{short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{short_text(r.get('entity_id'), 40)}</code>\n"
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
                f"  action: <code>{short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{short_text(r.get('entity_id'), 40)}</code>\n"
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
                f"  action: <code>{short_text(r.get('action'), 70)}</code>\n"
                f"  entity: <code>{r.get('entity_type') or '-'}</code> / <code>{short_text(r.get('entity_id'), 40)}</code>\n"
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
            f"  summary: <code>{short_text(r.get('summary'), 70)}</code>\n"
            f"  details: <code>{short_text(r.get('details'), 140)}</code>"
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


@router.message(F.text == "📋 Логи", IsBotAdminFilter(ADMINS))
async def logs_menu(message: types.Message):
    await message.answer(
        "📋 <b>Логи</b>\n\n"
        "В этой версии логирование доступно в консоли сервера и через middleware."
        " Если нужен просмотр логов внутри бота, это лучше вынести в отдельное хранилище.",
        parse_mode="HTML",
        reply_markup=logs_kb(),
    )


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
