# handlers/private/admin/finance.py
"""💰 Финансы и тарифы: суммы вывода, награды, чат отчётов о выплатах.
Сами выплаты (одобрение/отклонение) — в handlers/private/payout_guard.py."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from filters import private_only
from keyboards.reply_keyboards import (
    finance_kb,
    reply_keyboard_for_admin,
    settings_kb,
)
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS, url_yaml
from data.settings import get_settings
from services.payouts import payment_report_target
from utils.payout_report import parse_payment_chat_id
from utils.yaml_io import save_url_yaml

from .common import cancel_kb, forms_router, get_finance_totals, record_setting_change

router = private_only(Router(name="private.admin.finance"))


# Ключи совпадают с полями Settings: _current() читает значение через get_settings()
_TARIFF_FIELDS: dict[str, dict[str, Any]] = {
    "min_balance": {"label": "Мин. вывод", "icon": "💸", "allow_zero": True},
    "max_withdraw": {"label": "Макс. вывод", "icon": "💰", "allow_zero": False},
    "invited_credit": {"label": "За приглашение", "icon": "🎁", "allow_zero": True},
    "vote_credit": {"label": "За голос", "icon": "🗳", "allow_zero": True},
}


def _current(key: str) -> int:
    """
    Текущее значение тарифа так, как его видит бот (#49).
    Раньше экран показывал сырое url_yaml.get(key, 0): например, «Макс. вывод: 0»,
    хотя бот без заданного лимита считает 9 999 999, а строку "500" — числом 500.
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


@router.message(F.text == "💰 Финансы", IsBotAdminFilter(ADMINS))
async def finance_menu(message: types.Message):
    payouts = await get_finance_totals()
    payment_target = payment_report_target()
    payment_target_text = str(payment_target) if payment_target is not None else "не задан"
    await message.answer(
        "💰 <b>Финансы</b>\n\n"
        f"💸 Ожидают выплат: <b>{payouts['pending_payouts']}</b>\n"
        f"⭐ Условных единиц баланса: <b>{payouts['balance_units']}</b>\n"
        f"🆔 Чат отчётов выплат: <code>{payment_target_text}</code>\n\n"
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

    old_value = url_yaml.get(key)
    url_yaml[key] = value
    save_url_yaml()
    await record_setting_change(user.id, key, old_value, value)

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


@router.message(F.text == "💸 Мин. сумма вывода", IsBotAdminFilter(ADMINS))
async def edit_min_balance(message: types.Message):
    value = _current("min_balance")
    await message.answer(
        f"💸 Текущая минимальная сумма вывода: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("min_balance"),
    )


@router.callback_query(F.data == "yes_min_balance", IsBotAdminFilter(ADMINS))
async def confirm_min_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую минимальную сумму.\nСейчас: {_current('min_balance')}",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_min_balance)


@forms_router.message(AdminFlow.set_min_balance, IsBotAdminFilter(ADMINS))
async def update_min_balance(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>10000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("min_balance")
    url_yaml["min_balance"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "min_balance", old_value, url_yaml["min_balance"])
    await message.answer(
        f"✅ Минимальная сумма вывода изменена на <b>{url_yaml['min_balance']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


@router.message(F.text == "💰 Макс. сумма вывода", IsBotAdminFilter(ADMINS))
async def edit_max_withdraw(message: types.Message):
    value = _current("max_withdraw")
    await message.answer(
        f"💰 Текущая максимальная сумма вывода: <b>{value}</b> сум.\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("max_withdraw"),
    )


@router.callback_query(F.data == "yes_max_withdraw", IsBotAdminFilter(ADMINS))
async def confirm_max_withdraw(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую максимальную сумму.\nСейчас: {_current('max_withdraw')}\n"
        "Пример: <code>500000</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_max_withdraw)


@forms_router.message(AdminFlow.set_max_withdraw, IsBotAdminFilter(ADMINS))
async def update_max_withdraw(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>500000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("max_withdraw")
    url_yaml["max_withdraw"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "max_withdraw", old_value, url_yaml["max_withdraw"])
    await message.answer(
        f"✅ Максимальная сумма вывода изменена на <b>{url_yaml['max_withdraw']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


@router.message(F.text == "🎁 Сумма за приглашение", IsBotAdminFilter(ADMINS))
async def edit_invite_reward(message: types.Message):
    value = _current("invited_credit")
    await message.answer(
        f"👥 Текущая сумма за приглашённого: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("invite_reward"),
    )


@router.callback_query(F.data == "yes_invite_reward", IsBotAdminFilter(ADMINS))
async def confirm_invite_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую сумму за приглашение.\nСейчас: {_current('invited_credit')}",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_invite_reward)


@forms_router.message(AdminFlow.set_invite_reward, IsBotAdminFilter(ADMINS))
async def update_invite_reward(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>5000</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("invited_credit")
    url_yaml["invited_credit"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "invited_credit", old_value, url_yaml["invited_credit"])
    await message.answer(
        f"✅ Сумма за приглашение изменена на <b>{url_yaml['invited_credit']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


@router.message(F.text == "🗳 Сумма за голос", IsBotAdminFilter(ADMINS))
async def edit_vote_price(message: types.Message):
    value = _current("vote_credit")
    await message.answer(
        f"🗳 Текущая сумма за голос: <b>{value}</b> сум.\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("vote_reward"),
    )


@router.callback_query(F.data == "yes_vote_reward", IsBotAdminFilter(ADMINS))
async def confirm_vote_price(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новую сумму за голос.\nСейчас: {_current('vote_credit')}",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_vote_reward)


@forms_router.message(AdminFlow.set_vote_reward, IsBotAdminFilter(ADMINS))
async def update_vote_reward(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Введите только число!\nПример: <code>200</code>", parse_mode="HTML")
        return
    old_value = url_yaml.get("vote_credit")
    url_yaml["vote_credit"] = int(text)
    save_url_yaml()
    await record_setting_change(user.id, "vote_credit", old_value, url_yaml["vote_credit"])
    await message.answer(
        f"✅ Сумма за голос изменена на <b>{url_yaml['vote_credit']}</b> сум.",
        parse_mode="HTML", reply_markup=reply_keyboard_for_admin(),
    )
    await state.clear()


@router.message(F.text == "🆔 Чат выплат", IsBotAdminFilter(ADMINS))
async def edit_payment_chat_id(message: types.Message):
    value = url_yaml.get("payment_chat_id", "не задан")
    await message.answer(
        "🆔 Текущий payment_chat_id (для отчётов о выплатах):\n"
        f"<code>{value}</code>\n\n"
        "Поддерживается: <code>@channel_username</code> или <code>-1001234567890</code>.\n"
        "Хотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("payment_chat_id"),
    )


@router.callback_query(F.data == "yes_payment_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_payment_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        "Введите новый payment_chat_id.\n"
        f"Сейчас: {url_yaml.get('payment_chat_id', 'не задан')}\n"
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

    old_value = url_yaml.get("payment_chat_id", "не задан")
    url_yaml["payment_chat_id"] = parsed
    save_url_yaml()
    await record_setting_change(user.id, "payment_chat_id", old_value, url_yaml["payment_chat_id"])
    await message.answer(
        f"✅ payment_chat_id обновлён: <code>{url_yaml['payment_chat_id']}</code>",
        parse_mode="HTML",
        reply_markup=finance_kb(),
    )
    await state.clear()
