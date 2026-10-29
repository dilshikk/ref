# handlers/private/admin/common.py
"""
Общее для разделов админки (#37): клавиатура отмены, сводные счётчики,
запись изменений настроек в аудит.
Настройки сохраняются через utils.yaml_io.save_url_yaml (атомарная запись, #17).
"""
from typing import Any, cast
from aiogram import Router, types
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


async def get_user_totals() -> dict[str, int]:
    users = cast(list[dict[str, Any]], await db.select_all_users() or [])
    total_users = len(users)
    blocked_users = sum(1 for user in users if user.get("is_blocked"))
    return {"total_users": total_users, "blocked_users": blocked_users}


async def get_referral_totals() -> dict[str, int]:
    referrals = cast(dict[str, Any] | None, await db.execute(
        "SELECT COALESCE(SUM(history_count), 0) AS total_referrals, COALESCE(SUM(current_count), 0) AS current_referrals FROM UserInviteCount",
        fetchone=True,
    ))
    return {
        "total_referrals": int(referrals["total_referrals"] or 0) if referrals else 0,
        "current_referrals": int(referrals["current_referrals"] or 0) if referrals else 0,
    }


async def get_channel_totals() -> dict[str, int]:
    channels = cast(list[dict[str, Any]], await db.get_all_channels() or [])
    return {"total_channels": len(channels)}


async def get_finance_totals() -> dict[str, int]:
    payouts = cast(list[dict[str, Any]], await db.get_pending_payouts() or [])
    counts = cast(dict[str, Any] | None, await db.execute(
        "SELECT COALESCE(SUM(current_count), 0) AS balance_units FROM UserInviteCount",
        fetchone=True,
    ))
    balance_units = int(counts["balance_units"] or 0) if counts else 0
    return {"pending_payouts": len(payouts), "balance_units": balance_units}


async def show_placeholder(message: types.Message, title: str, body: str, keyboard: ReplyKeyboardMarkup) -> None:
    await message.answer(f"<b>{title}</b>\n\n{body}", parse_mode="HTML", reply_markup=keyboard)


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
