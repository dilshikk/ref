# utils/payout_report.py
"""
Чистая логика отчёта о выплате (#36): куда отправлять и что писать.
Без зависимостей от бота и БД, чтобы её можно было покрыть тестами
(по образцу utils/payout_math.py). Отправка — в services/payouts.py.
"""
import html
from collections.abc import Mapping
from datetime import datetime
from typing import Any

ChatTarget = int | str


def parse_payment_chat_id(value: str) -> ChatTarget | None:
    """'-1001234567890' → int, '@channel' → str, иначе None."""
    raw = value.strip()
    if raw.startswith("-100") and raw[1:].isdigit():
        return int(raw)
    if raw.startswith("@") and len(raw) > 1 and " " not in raw:
        return raw
    return None


def resolve_payment_report_target(config: Mapping[str, Any]) -> ChatTarget | None:
    """Чат для отчётов: payment_chat_id, а если он не задан или кривой — payment_channel.
    Число из YAML принимается только для payment_chat_id (так было и раньше)."""
    chat_id = config.get("payment_chat_id")
    if isinstance(chat_id, int) and not isinstance(chat_id, bool):
        return chat_id
    for raw in (chat_id, config.get("payment_channel")):
        if isinstance(raw, str):
            parsed = parse_payment_chat_id(raw)
            if parsed is not None:
                return parsed
    return None


def mask_card(card_number: object) -> str:
    """Последние 4 цифры карты: ****1234. Короче 4 символов — '-'."""
    raw = str(card_number or "")
    return f"****{raw[-4:]}" if len(raw) >= 4 else "-"


def build_payout_report_text(
    payout: Mapping[str, Any],
    admin_id: int,
    include_receipt: bool,
    now: datetime,
) -> str:
    """Текст отчёта (HTML). Имя пользователя экранируется: символы < > & в имени
    раньше ломали разметку, и Telegram отказывался отправлять отчёт."""
    amount = payout.get("amount")
    amount_text = html.escape(str(amount)) if amount is not None else "-"
    user_id = html.escape(str(payout.get("user_id") or "-"), quote=True)
    full_name = html.escape(str(payout.get("full_name") or "-"))
    req_id = html.escape(str(payout.get("id") or "-"))
    return (
        "💸 <b>Отчет о выплате</b>\n\n"
        f"🆔 Заявка: <code>{req_id}</code>\n"
        f"👤 Пользователь: <a href='tg://user?id={user_id}'>{full_name}</a>\n"
        f"🧾 User ID: <code>{user_id}</code>\n"
        f"💰 Сумма: <b>{amount_text}</b>\n"
        f"💳 Карта: <code>{mask_card(payout.get('card_number'))}</code>\n"
        f"👮 Подтвердил админ: <code>{admin_id}</code>\n"
        f"🕒 Время: <b>{now.strftime('%Y-%m-%d %H:%M:%S')}</b>\n"
        f"📎 Чек: <b>{'приложен' if include_receipt else 'не приложен'}</b>"
    )
