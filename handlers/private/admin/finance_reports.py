# handlers/private/admin/finance_reports.py
"""
💰 Финансы: экраны, у кнопок которых раньше не было обработчиков.

- «💳 Балансы» (#68): сколько денег на балансах, сколько из них «на проверке»
  (#46, нельзя вывести сейчас), долги, заявки в ожидании, топ-10 по балансу.
- «📊 Отчёты» (#72): сводка за 24ч / 7д / 30д — заявки на вывод по статусам,
  начисления за приглашённых и голоса, снятия за выход, ручные бонусы.
- «🎁 Бонусы» (#71): последние ручные начисления из audit_logs
  (action='admin_balance_changed', пишет user_management.add_balance_process).

Только чтение: ни один обработчик здесь не меняет деньги.
"""
import html
from typing import Any, cast

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data.config import ADMINS
from data.settings import get_settings
from filters import private_only
from filters.admin import IsBotAdminFilter
from keyboards.reply_keyboards import finance_kb
from loader import db
from services.group_adds import qualification_enabled
from utils.db.unverified_sql import unverified_sum_sql
from utils.finance_text import (
    BONUS_LIST_LIMIT,
    DEFAULT_REPORT_DAYS,
    REPORT_CALLBACK_PREFIX,
    REPORT_PERIODS,
    TOP_BALANCES_LIMIT,
    money,
    parse_period,
    to_int,
    user_label,
)

from .common import get_finance_totals

router = private_only(Router(name="private.admin.finance_reports"))

# Сумма ручного бонуса из записи аудита: приглашений × цена на момент начисления
_BONUS_SUM_SQL = (
    "COALESCE((a.payload->>'amount')::bigint * (a.payload->>'invited_credit')::bigint, 0)"
)


async def _row(sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = cast(dict[str, Any] | None, await db.execute(sql, parameters=parameters, fetchone=True))
    return dict(row or {})


async def _rows(sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = cast(list[dict[str, Any]], await db.execute(sql, parameters=parameters, fetchall=True) or [])
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# 💳 Балансы (#68)
# ─────────────────────────────────────────────
async def _locked_total() -> int:
    """
    Деньги «на проверке» по всем пользователям (тот же SQL, что при подаче и
    одобрении заявки, #46). У каждого не больше его баланса: долг не учитываем.
    """
    settings = get_settings()
    locked = unverified_sum_sql("uic.user_id", "CAST($1 AS INT)", "CAST($2 AS BIGINT)", "CAST($3 AS BOOLEAN)")
    row = await _row(
        f"""
        SELECT COALESCE(SUM(LEAST(t.locked, t.balance_sum)), 0)::bigint AS locked_sum
        FROM (
            SELECT uic.balance_sum, {locked} AS locked
            FROM UserInviteCount uic
            WHERE uic.balance_sum > 0
        ) t
        """,
        (int(settings.referral_hold_hours), settings.group_id, bool(qualification_enabled())),
    )
    return to_int(row.get("locked_sum"))


async def _balances_text() -> str:
    totals = await get_finance_totals()
    counts = await _row(
        """
        SELECT
            COUNT(*) FILTER (WHERE balance_sum > 0) AS with_balance,
            COUNT(*) FILTER (WHERE balance_sum < 0) AS in_debt
        FROM UserInviteCount
        """
    )
    locked = await _locked_total()
    available = max(0, totals["balance_sum"] - locked)
    top = await _rows(
        """
        SELECT uic.user_id, uic.balance_sum, u.full_name, u.username
        FROM UserInviteCount uic
        LEFT JOIN Users u ON u.telegram_id = uic.user_id
        WHERE uic.balance_sum > 0
        ORDER BY uic.balance_sum DESC
        LIMIT $1
        """,
        (TOP_BALANCES_LIMIT,),
    )

    lines = [
        "💳 <b>Балансы</b>\n",
        f"💵 На балансах: <b>{money(totals['balance_sum'])}</b> сум "
        f"у <b>{to_int(counts.get('with_balance'))}</b> пользователей",
        f"   ⏳ из них на проверке: <b>{money(locked)}</b> сум",
        f"   ✅ можно вывести сейчас: <b>{money(available)}</b> сум",
        f"💸 Ожидают выплаты: <b>{totals['pending_payouts']}</b> заявок на <b>{money(totals['pending_sum'])}</b> сум",
    ]
    if totals["debt_sum"] > 0:
        lines.append(
            f"⚠️ Долги: <b>{money(totals['debt_sum'])}</b> сум "
            f"у <b>{to_int(counts.get('in_debt'))}</b> пользователей"
        )

    lines.append(f"\n🏆 <b>Топ-{TOP_BALANCES_LIMIT} по балансу</b>")
    if top:
        lines += [
            f"{i}. {user_label(r.get('full_name'), r.get('username'), r.get('user_id'))}: "
            f"<b>{money(r.get('balance_sum'))}</b> сум"
            for i, r in enumerate(top, start=1)
        ]
    else:
        lines.append("Пока ни у кого нет денег на балансе.")
    lines.append("\nНажмите /uid рядом с именем, чтобы открыть профиль.")
    return "\n".join(lines)


@router.message(F.text == "💳 Балансы", IsBotAdminFilter(ADMINS))
async def finance_balances(message: types.Message) -> None:
    await message.answer(await _balances_text(), parse_mode="HTML", reply_markup=finance_kb())


# ─────────────────────────────────────────────
# 📊 Отчёты (#72)
# ─────────────────────────────────────────────
async def _report_text(days: int) -> str:
    params = (days,)
    since = "NOW() - make_interval(days => CAST($1 AS INT))"
    payouts = await _row(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status = 'approved') AS approved_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'approved'), 0) AS approved_sum,
            COUNT(*) FILTER (WHERE status = 'rejected') AS rejected_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'rejected'), 0) AS rejected_sum,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'pending'), 0) AS pending_sum
        FROM PayoutRequests
        WHERE created_at >= {since}
        """,
        params,
    )
    referrals = await _row(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE reversed_at IS NULL) AS credited_count,
            COALESCE(SUM(reward_sum) FILTER (WHERE reversed_at IS NULL), 0) AS credited_sum,
            COUNT(*) FILTER (WHERE reversed_at IS NOT NULL) AS reversed_count,
            COALESCE(SUM(reward_sum) FILTER (WHERE reversed_at IS NOT NULL), 0) AS reversed_sum
        FROM UserInviteMember
        WHERE invited_at >= {since}
        """,
        params,
    )
    votes = await _row(
        f"""
        SELECT COUNT(*) AS vote_count, COALESCE(SUM(reward_sum), 0) AS vote_sum
        FROM poll_vote_rewards
        WHERE created_at >= {since}
        """,
        params,
    )
    bonuses = await _row(
        f"""
        SELECT COUNT(*) AS bonus_count, COALESCE(SUM({_BONUS_SUM_SQL}), 0) AS bonus_sum
        FROM audit_logs a
        WHERE a.action = 'admin_balance_changed' AND a.created_at >= {since}
        """,
        params,
    )
    credited_total = (
        to_int(referrals.get("credited_sum")) + to_int(votes.get("vote_sum")) + to_int(bonuses.get("bonus_sum"))
    )
    return (
        f"📊 <b>Отчёт за {REPORT_PERIODS[days]}</b>\n\n"
        "<b>Заявки на вывод</b> (по дате подачи)\n"
        f"✅ Одобрено: <b>{to_int(payouts.get('approved_count'))}</b> на <b>{money(payouts.get('approved_sum'))}</b> сум\n"
        f"❌ Отклонено: <b>{to_int(payouts.get('rejected_count'))}</b> на <b>{money(payouts.get('rejected_sum'))}</b> сум\n"
        f"⏳ Ждут решения: <b>{to_int(payouts.get('pending_count'))}</b> на <b>{money(payouts.get('pending_sum'))}</b> сум\n\n"
        "<b>Начисления</b>\n"
        f"🎁 За приглашённых: <b>{to_int(referrals.get('credited_count'))}</b> на <b>{money(referrals.get('credited_sum'))}</b> сум\n"
        f"🗳 За голоса: <b>{to_int(votes.get('vote_count'))}</b> на <b>{money(votes.get('vote_sum'))}</b> сум\n"
        f"➕ Ручные бонусы: <b>{to_int(bonuses.get('bonus_count'))}</b> на <b>{money(bonuses.get('bonus_sum'))}</b> сум\n"
        f"📈 Всего начислено: <b>{money(credited_total)}</b> сум\n\n"
        f"↩️ Снято за выход из группы: <b>{to_int(referrals.get('reversed_count'))}</b> "
        f"на <b>{money(referrals.get('reversed_sum'))}</b> сум"
    )


def _report_kb(active_days: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"• {label} •" if days == active_days else label,
            callback_data=f"{REPORT_CALLBACK_PREFIX}{days}",
        )
        for days, label in REPORT_PERIODS.items()
    ]])


@router.message(F.text == "📊 Отчёты", IsBotAdminFilter(ADMINS))
async def finance_reports(message: types.Message) -> None:
    await message.answer(
        await _report_text(DEFAULT_REPORT_DAYS),
        parse_mode="HTML",
        reply_markup=_report_kb(DEFAULT_REPORT_DAYS),
    )


@router.callback_query(F.data.startswith(REPORT_CALLBACK_PREFIX), IsBotAdminFilter(ADMINS))
async def finance_report_period(call: types.CallbackQuery) -> None:
    days = parse_period(call.data)
    if days is None or call.message is None:
        await call.answer("Неизвестный период")
        return
    try:
        await cast(Any, call.message).edit_text(
            await _report_text(days),
            parse_mode="HTML",
            reply_markup=_report_kb(days),
        )
    except TelegramBadRequest:
        # «message is not modified» — повторное нажатие того же периода
        pass
    await call.answer()


# ─────────────────────────────────────────────
# 🎁 Бонусы (#71)
# ─────────────────────────────────────────────
async def _bonuses_text() -> str:
    totals = await _row(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE a.created_at >= NOW() - INTERVAL '30 days') AS count_30d,
            COALESCE(SUM({_BONUS_SUM_SQL}) FILTER (WHERE a.created_at >= NOW() - INTERVAL '30 days'), 0) AS sum_30d,
            COUNT(*) AS count_all,
            COALESCE(SUM({_BONUS_SUM_SQL}), 0) AS sum_all
        FROM audit_logs a
        WHERE a.action = 'admin_balance_changed'
        """
    )
    rows = await _rows(
        f"""
        SELECT a.created_at,
               a.actor_id,
               a.entity_id,
               {_BONUS_SUM_SQL} AS bonus_sum,
               u.full_name, u.username,
               adm.full_name AS admin_name
        FROM audit_logs a
        LEFT JOIN Users u ON u.telegram_id::text = a.entity_id
        LEFT JOIN Users adm ON adm.telegram_id = a.actor_id
        WHERE a.action = 'admin_balance_changed'
        ORDER BY a.created_at DESC
        LIMIT $1
        """,
        (BONUS_LIST_LIMIT,),
    )
    head = (
        "🎁 <b>Бонусы</b> (ручные начисления из профиля)\n\n"
        f"За 30 дней: <b>{to_int(totals.get('count_30d'))}</b> на <b>{money(totals.get('sum_30d'))}</b> сум\n"
        f"За всё время: <b>{to_int(totals.get('count_all'))}</b> на <b>{money(totals.get('sum_all'))}</b> сум\n"
    )
    if not rows:
        return head + "\nРучных начислений пока не было. Начислить: профиль пользователя → «➕ Начислить»."

    lines = []
    for r in rows:
        created = r.get("created_at")
        when = created.strftime("%d.%m.%Y %H:%M") if created else "—"
        admin = html.escape(str(r.get("admin_name") or r.get("actor_id") or "—"))
        lines.append(
            f"• {when} — <b>+{money(r.get('bonus_sum'))}</b> сум\n"
            f"   кому: {user_label(r.get('full_name'), r.get('username'), r.get('entity_id'))}\n"
            f"   кто: {admin}"
        )
    return head + f"\n<b>Последние {len(rows)}</b>\n" + "\n".join(lines)


@router.message(F.text == "🎁 Бонусы", IsBotAdminFilter(ADMINS))
async def finance_bonuses(message: types.Message) -> None:
    await message.answer(await _bonuses_text(), parse_mode="HTML", reply_markup=finance_kb())
