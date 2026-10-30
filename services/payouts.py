# services/payouts.py
"""
Отчёты о выплатах (#36). Раньше жили внутри handlers/private/admin_panel.py,
и payout_guard импортировал оттуда приватную _send_payout_report.
Теперь и payout_guard, и меню «💰 Финансы» берут публичные функции отсюда.
"""
from datetime import datetime
from typing import Any

from aiogram import types

from data.settings import snapshot
from utils.payout_report import ChatTarget, build_payout_report_text, resolve_payment_report_target


def payment_report_target() -> ChatTarget | None:
    """Текущий чат отчётов. Настройки меняются на лету из админки (#113), поэтому читаем при каждом вызове."""
    return resolve_payment_report_target(snapshot())


async def send_payout_report(
    message: types.Message,
    payout: dict[str, Any],
    admin_id: int,
    include_receipt: bool,
) -> bool:
    """Отправляет отчёт в чат выплат; при include_receipt копирует туда сообщение с чеком.
    False — если чат отчётов не задан или нет бота."""
    bot = message.bot
    if bot is None:
        return False

    target = payment_report_target()
    if target is None:
        return False

    text = build_payout_report_text(payout, admin_id=admin_id, include_receipt=include_receipt, now=datetime.now())
    await bot.send_message(target, text, parse_mode="HTML")
    if include_receipt:
        await message.copy_to(target)
    return True
