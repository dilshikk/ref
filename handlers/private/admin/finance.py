# handlers/private/admin/finance.py
"""💰 Финансы и тарифы: суммы вывода, награды, чат отчётов о выплатах, денежные админы.
Сами выплаты (одобрение/отклонение) — в handlers/private/payout_guard.py.

Тарифы меняются только в инлайн-панели «💰 Тарифы» (#99). Старые обработчики
«💸 Мин. сумма вывода», «💰 Макс. сумма вывода», «🎁 Сумма за приглашение»,
«🗳 Сумма за голос» удалены: их кнопки (tariffs_kb) нигде не показывались.

Настройки хранятся в bot_settings (#113): читаем через get_settings() / get_raw(),
сохраняем через set_setting()."""
import html
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from filters import private_only
from keyboards.reply_keyboards import (
    finance_kb,
    settings_kb,
)
from loader import db
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_raw, get_settings, is_http_url, parse_user_ids
from services.payouts import payment_report_target
from services.settings_store import set_setting
from utils.payout_report import parse_payment_chat_id

from .common import cancel_kb, forms_router, get_finance_totals, record_setting_change

router = private_only(Router(name="private.admin.finance"))


# Ключи совпадают с полями Settings: _current() читает значение через get_settings()
_TARIFF_FIELDS: dict[str, dict[str, Any]] = {
    "min_balance": {"label": "Мин. вывод", "icon": "💸", "allow_zero": True},
    "max_withdraw": {"label": "Макс. вывод", "icon": "💰", "allow_zero": False},
    "invited_credit": {"label": "За приглашение", "icon": "🎁", "allow_zero": True},
    "vote_credit": {"label": "За голос", "icon": "🗳", "allow_zero": True},
}


async def _save(user_id: int, key: str, value: Any) -> None:
    """Сохраняет настройку в базу и пишет изменение в журнал."""
    old_value = await set_setting(key, value, user_id)
    await record_setting_change(user_id, key, old_value, value)


def _current(key: str) -> int:
    """
    Текущее значение тарифа так, как его видит бот (#49): без заданного лимита
    бот считает 9 999 999, а строку "500" — числом 500.
    """
    return int(getattr(get_settings(), key))


def _tariffs_text() -> str:
    return (
        "💰 <b>Тарифы</b>\n\n"
        f"Мин. вывод: <b>{_current('min_balance')}</b>\n"
        f"Макс. вывод: <b>{_current('max_withdraw')}</b>\n"
        f"За приглашение: <b>{_current('invited_credit')}</b>\n"
        f"За голос: <b>{_current('vote_credit')}</b>\n\n"
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
        "Здесь собраны денежные настройки и отчёты.",
        parse_mode="HTML",
        reply_markup=finance_kb(),
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
        f"Сейчас: <b>{_current(key)}</b>",
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


@forms_router.message(AdminFlow.set_tariff_inline_value, IsBotAdminFilter(ADMINS))
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
        await message.answer("❌ Введите только целое число.", reply_markup=cancel_kb)
        return

    value = int(raw)
    if not bool(field.get("allow_zero", True)) and value <= 0:
        await message.answer("❌ Значение должно быть больше нуля.", reply_markup=cancel_kb)
        return

    await _save(user.id, key, value)

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


# ─────────────────────────────────────────────
# 🆔 Чат выплат: чат отчётов (payment_chat_id) и публичная ссылка (payment_channel, #107)
# ─────────────────────────────────────────────
def _payment_chat_id_text() -> str:
    value = get_raw("payment_chat_id")
    return str(value) if value not in (None, "") else "не задан"


def _payment_chat_text() -> str:
    link = get_settings().payment_channel or "не задана"
    return (
        "🆔 <b>Чат выплат</b>\n\n"
        f"🤖 Чат для отчётов о выплатах (сюда пишет бот):\n<code>{html.escape(_payment_chat_id_text())}</code>\n\n"
        "👥 Публичная ссылка для пользователей (кнопка «📢 Чат выплат»):\n"
        f"{html.escape(link)}\n\n"
        "Что изменить?"
    )


def _payment_chat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Чат отчётов", callback_data="yes_payment_chat_id")],
        [InlineKeyboardButton(text="👥 Ссылка для пользователей", callback_data="payment_channel_edit")],
        # «no» обрабатывает admin/menu.py::cancel_edit
        [InlineKeyboardButton(text="❌ Ничего", callback_data="no")],
    ])


@router.message(F.text == "🆔 Чат выплат", IsBotAdminFilter(ADMINS))
async def edit_payment_chat_id(message: types.Message):
    await message.answer(_payment_chat_text(), parse_mode="HTML", reply_markup=_payment_chat_kb())


@router.callback_query(F.data == "yes_payment_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_payment_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    await cast(Any, msg).answer(
        "Введите новый payment_chat_id.\n"
        f"Сейчас: {html.escape(_payment_chat_id_text())}\n"
        "Примеры: <code>@my_channel</code> или <code>-1001234567890</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_payment_chat_id)


@forms_router.message(AdminFlow.set_payment_chat_id, IsBotAdminFilter(ADMINS))
async def update_payment_chat_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    parsed = parse_payment_chat_id(text)
    if parsed is None:
        await message.answer(
            "❌ Неверный формат!\n"
            "Используйте <code>@channel_username</code> или <code>-1001234567890</code>.",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    await _save(user.id, "payment_chat_id", parsed)
    await message.answer(
        f"✅ payment_chat_id обновлён: <code>{html.escape(str(parsed))}</code>",
        parse_mode="HTML",
        reply_markup=finance_kb(),
    )
    await state.clear()


@router.callback_query(F.data == "payment_channel_edit", IsBotAdminFilter(ADMINS))
async def confirm_payment_channel(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    await cast(Any, msg).answer(
        "Введите публичную ссылку на чат/канал выплат — её увидят пользователи по кнопке «📢 Чат выплат».\n"
        "Пример: <code>https://t.me/my_payouts</code>\n\n"
        "Отправьте <code>-</code>, чтобы убрать ссылку.",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_payment_channel)


@forms_router.message(AdminFlow.set_payment_channel, IsBotAdminFilter(ADMINS))
async def update_payment_channel(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if text != "-" and not is_http_url(text):
        await message.answer(
            "❌ Нужна ссылка, начинающаяся с https://\nПример: <code>https://t.me/my_payouts</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    new_value = "" if text == "-" else text
    await _save(user.id, "payment_channel", new_value)
    result = f"✅ Ссылка обновлена: {html.escape(new_value)}" if new_value else "✅ Ссылка убрана."
    await message.answer(result, parse_mode="HTML", reply_markup=finance_kb())
    await state.clear()


# ─────────────────────────────────────────────
# 👑 Денежные админы (#116): кто одобряет выплаты и меняет балансы.
# Раньше задавались только MONEY_ADMIN / MONEY_ADMINS в .env.
# ─────────────────────────────────────────────
def can_edit_money_admins(user_id: int) -> bool:
    """Пока денежных админов нет — назначает любой админ бота.
    Когда они есть — менять список может только денежный админ:
    иначе обычный админ мог бы сам дать себе доступ к деньгам."""
    current = get_settings().money_admins
    return not current or user_id in current


def _money_admins_text() -> str:
    current = get_settings().money_admins
    listed = "\n".join(f"• <code>{a}</code>" + ("" if a in ADMINS else " ⚠️ не в ADMINS") for a in current)
    return (
        "👑 <b>Денежные админы</b>\n\n"
        "Они одобряют выплаты и меняют балансы.\n\n"
        + (listed or "Пока никто не назначен.")
    )


@router.message(F.text == "👑 Денежные админы", IsBotAdminFilter(ADMINS))
async def money_admins_menu(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    if not can_edit_money_admins(user.id):
        await message.answer(_money_admins_text() + "\n\n🔒 Изменить список может только денежный админ.",
                             parse_mode="HTML", reply_markup=finance_kb())
        return
    await message.answer(
        _money_admins_text()
        + "\n\nОтправьте Telegram ID через запятую, чтобы заменить список.\n"
        "Пример: <code>111111111, 222222222</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_money_admins)


@forms_router.message(AdminFlow.set_money_admins, IsBotAdminFilter(ADMINS))
async def update_money_admins(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    # Проверяем ещё раз: список мог измениться, пока форма была открыта
    if not can_edit_money_admins(user.id):
        await message.answer("🔒 Изменить список может только денежный админ.", reply_markup=finance_kb())
        await state.clear()
        return
    ids = parse_user_ids(message.text or "")
    if ids is None:
        await message.answer(
            "❌ Нужны числовые Telegram ID через запятую.\nПример: <code>111111111, 222222222</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return
    await _save(user.id, "money_admins", list(ids))
    not_admins = [a for a in ids if a not in ADMINS]
    warning = (
        f"\n\n⚠️ {', '.join(map(str, not_admins))} нет в ADMINS: они не увидят админ-панель и заявки."
        if not_admins else ""
    )
    await message.answer("✅ Список обновлён.\n\n" + _money_admins_text() + warning,
                         parse_mode="HTML", reply_markup=finance_kb())
    await state.clear()
