# handlers/private/admin/broadcasts.py
"""📨 Рассылки: меню раздела, статистика, планирование, отмена.
Создание и отправка рассылок — в handlers/private/broadcasts.py (#19)."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import broadcasts_kb
from loader import db
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from services.audit_log import audit_log_service

from .common import cancel_kb

router = private_only(Router(name="private.admin.broadcasts"))


@router.message(F.text == "📨 Рассылки", IsBotAdminFilter(ADMINS))
async def broadcasts_menu(message: types.Message):
    await message.answer(
        "📨 <b>Рассылки</b>\n\n"
        "Создайте новую рассылку или вернитесь в главное меню.",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


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


@router.callback_query(F.data == "plan_send_ad", IsBotAdminFilter(ADMINS))
async def plan_send_ad(call: types.CallbackQuery, state: FSMContext):
    if call.message is None:
        return
    await cast(Any, call.message).answer(
        "Введите дату и время отправки в формате:\n"
        "<code>YYYY-MM-DD HH:MM</code>\n"
        "Пример: <code>2026-07-23 18:30</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.ask_ad_schedule_at)


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
