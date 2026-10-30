# handlers/private/admin/finance.py
"""💰 Финансы: сводка раздела. Только деньги и отчёты — без настроек.

Раньше здесь были ещё «💰 Тарифы», «🆔 Чат выплат» и «👑 Денежные админы»,
и тарифы одновременно открывались из «⚙️ Настройки». Теперь:
- суммы, награды и чат выплат — «⚙️ Настройки» (settings.py);
- денежные админы — «👥 Пользователи» → «👑 Администраторы» (users.py).

Сами выплаты (одобрение/отклонение) — в handlers/private/payout_guard.py,
балансы, отчёты и бонусы — в finance_reports.py.
"""
import html
from typing import Any, cast
from aiogram import Router, types, F
from filters import private_only
from keyboards.reply_keyboards import finance_kb
from loader import db
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from services.payouts import payment_report_target

from .common import get_finance_totals

router = private_only(Router(name="private.admin.finance"))


async def _holds_summary() -> tuple[int, int]:
    """(вступлений на удержании, пользователей с удержаниями) — для шапки раздела (#101)."""
    rows = cast(list[dict[str, Any]], await db.list_pending_referral_holds() or [])
    return sum(int(row.get("held") or 0) for row in rows), len(rows)


@router.message(F.text == "💰 Финансы", IsBotAdminFilter(ADMINS))
async def finance_menu(message: types.Message):
    totals = await get_finance_totals()
    held, held_users = await _holds_summary()
    payment_target = payment_report_target()
    payment_target_text = str(payment_target) if payment_target is not None else "не задан"
    await message.answer(
        "💰 <b>Финансы</b>\n\n"
        f"💸 Ожидают выплат: <b>{totals['pending_payouts']}</b> на <b>{totals['pending_sum']}</b> сум\n"
        # #101: админ сразу видит, что есть отложенные начисления
        f"⏸ На удержании: <b>{held}</b> вступлений у <b>{held_users}</b> пользователей\n"
        # #84: раньше здесь было число приглашённых под видом «единиц баланса»
        f"💵 На балансах пользователей: <b>{totals['balance_sum']}</b> сум\n"
        f"🆔 Чат отчётов выплат: <code>{html.escape(payment_target_text)}</code>\n\n"
        "Суммы, награды и чат выплат меняются в «⚙️ Настройки».",
        parse_mode="HTML",
        reply_markup=finance_kb(),
    )
