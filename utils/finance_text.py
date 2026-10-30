# utils/finance_text.py
"""
Чистые функции для экранов «💰 Финансы» (#68, #71, #72): форматирование сумм,
разбор периода отчёта, подпись пользователя. Без aiogram и настроек бота —
проверяются тестами tests/test_finance_reports.py.
"""
from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation
from typing import Any

# Периоды отчёта: дни → подпись. Другие значения из callback_data не принимаются.
REPORT_PERIODS: dict[int, str] = {1: "24 часа", 7: "7 дней", 30: "30 дней"}
DEFAULT_REPORT_DAYS = 7
REPORT_CALLBACK_PREFIX = "fin_report:"

TOP_BALANCES_LIMIT = 10
BONUS_LIST_LIMIT = 20


def to_int(value: Any) -> int:
    """Число из строки БД (int, Decimal из NUMERIC, None). Мусор → 0."""
    if value is None:
        return 0
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def money(value: Any) -> str:
    """12345 → «12 345». Разделитель разрядов — пробел, как принято для сумов."""
    return f"{to_int(value):,}".replace(",", " ")


def parse_period(data: str | None) -> int | None:
    """«fin_report:7» → 7. Неизвестный или испорченный период → None."""
    if not data or not data.startswith(REPORT_CALLBACK_PREFIX):
        return None
    raw = data[len(REPORT_CALLBACK_PREFIX):]
    if not raw.isdigit():
        return None
    days = int(raw)
    return days if days in REPORT_PERIODS else None


def user_label(full_name: Any, username: Any, telegram_id: Any) -> str:
    """«Имя (@username) — /uid123». Имя и username экранируются: их задаёт пользователь (#50)."""
    name = html.escape(str(full_name)) if full_name else "—"
    nick = f"@{html.escape(str(username))}" if username else "—"
    return f"{name} ({nick}) — /uid{to_int(telegram_id)}"
