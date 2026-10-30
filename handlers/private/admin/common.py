# handlers/private/admin/common.py
"""
Общее для разделов админки (#37): клавиатура отмены, сводные счётчики,
запись изменений настроек в аудит.
Настройки сохраняются через services.settings_store.set_setting в таблицу bot_settings (#115).
"""
import html
from typing import Any, cast
from aiogram import Router
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from filters import private_only
from loader import db
from services.audit_log import audit_log_service

# Ввод значений в формах (обработчики со state AdminFlow.*) живёт в отдельном
# роутере, который подключается ПОСЛЕДНИМ в пакете admin. Поэтому кнопка меню,
# нажатая посреди заполнения формы, всегда открывает свой раздел, а не уходит
# в форму как введённое значение. Раньше результат зависел от того, где
# в 110-КБ файле стоял обработчик кнопки — выше или ниже обработчика формы.
forms_router = private_only(Router(name="private.admin.forms"))


cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True,
)

BLOCKED_LIST_LIMIT = 30


def _int(row: dict[str, Any] | None, key: str) -> int:
    return int((row or {}).get(key) or 0)


async def get_user_totals() -> dict[str, int]:
    # Раньше грузились ВСЕ пользователи ради двух чисел
    row = cast(dict[str, Any] | None, await db.execute(
        "SELECT COUNT(*) AS total_users, COUNT(*) FILTER (WHERE is_blocked) AS blocked_users FROM Users",
        fetchone=True,
    ))
    return {"total_users": _int(row, "total_users"), "blocked_users": _int(row, "blocked_users")}


async def get_referral_totals() -> dict[str, int]:
    referrals = cast(dict[str, Any] | None, await db.execute(
        "SELECT COALESCE(SUM(history_count), 0) AS total_referrals, COALESCE(SUM(current_count), 0) AS current_referrals FROM UserInviteCount",
        fetchone=True,
    ))
    return {
        "total_referrals": _int(referrals, "total_referrals"),
        "current_referrals": _int(referrals, "current_referrals"),
    }


async def get_channel_totals() -> dict[str, int]:
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    return {"total_channels": len(channels)}


async def get_finance_totals() -> dict[str, int]:
    """
    Деньги в сумах (#84). Раньше «финансы» считали SUM(current_count) —
    это число приглашённых, а не деньги. Баланс хранится в balance_sum / earned_sum.
    """
    payouts = cast(dict[str, Any] | None, await db.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending') AS pending_payouts,
            COALESCE(SUM(amount) FILTER (WHERE status = 'pending'), 0) AS pending_sum,
            COUNT(*) FILTER (WHERE status = 'approved') AS approved_payouts,
            COALESCE(SUM(amount) FILTER (WHERE status = 'approved'), 0) AS approved_sum,
            COALESCE(SUM(amount) FILTER (
                WHERE status = 'approved' AND created_at >= NOW() - INTERVAL '30 days'
            ), 0) AS approved_30d_sum
        FROM PayoutRequests
        """,
        fetchone=True,
    ))
    balances = cast(dict[str, Any] | None, await db.execute(
        """
        SELECT
            COALESCE(SUM(GREATEST(balance_sum, 0)), 0) AS balance_sum,
            COALESCE(-SUM(LEAST(balance_sum, 0)), 0) AS debt_sum,
            COALESCE(SUM(earned_sum), 0) AS earned_sum
        FROM UserInviteCount
        """,
        fetchone=True,
    ))
    return {
        "pending_payouts": _int(payouts, "pending_payouts"),
        "pending_sum": _int(payouts, "pending_sum"),
        "approved_payouts": _int(payouts, "approved_payouts"),
        "approved_sum": _int(payouts, "approved_sum"),
        "approved_30d_sum": _int(payouts, "approved_30d_sum"),
        "balance_sum": _int(balances, "balance_sum"),
        "debt_sum": _int(balances, "debt_sum"),
        "earned_sum": _int(balances, "earned_sum"),
    }


async def blocked_users_text() -> str:
    """Список заблокированных с переходом в профиль (#78). Раньше было только число."""
    title = "🚫 <b>Заблокированные пользователи</b>\n\n"
    total = (await get_user_totals())["blocked_users"]
    if total == 0:
        return title + "Сейчас заблокированных пользователей нет."

    rows = cast(list[dict[str, Any]], await db.execute(
        """
        SELECT telegram_id, full_name, username
        FROM Users
        WHERE is_blocked
        ORDER BY created_at DESC
        LIMIT $1
        """,
        parameters=(BLOCKED_LIST_LIMIT,),
        fetchall=True,
    ) or [])
    lines = []
    for row in rows:
        name = html.escape(row.get("full_name") or "—")
        username = f"@{html.escape(row['username'])}" if row.get("username") else "—"
        lines.append(f"• {name} ({username}) — /uid{row['telegram_id']}")
    more = total - len(rows)
    tail = f"\n…и ещё <b>{more}</b>" if more > 0 else ""
    return (
        title
        + f"Всего: <b>{total}</b>\n\n"
        + "\n".join(lines)
        + tail
        + "\n\nНажмите /uid рядом с именем, чтобы открыть профиль и разблокировать."
    )


async def record_setting_change(actor_id: int, setting_name: str, old_value, new_value) -> None:
    await audit_log_service.record_admin_action(
        actor_id=actor_id,
        action="setting_changed",
        entity_type="setting",
        entity_id=setting_name,
        old_value={setting_name: old_value},
        new_value={setting_name: new_value},
        payload={"setting": setting_name},
    )


def short_text(value: Any, max_len: int = 120) -> str:
    text = str(value or "")
    return text if len(text) <= max_len else f"{text[:max_len - 3]}..."
