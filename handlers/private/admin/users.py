# handlers/private/admin/users.py
"""👥 Пользователи: списки, заблокированные, администраторы и денежные админы.
Поиск, профиль и изменение баланса — в handlers/private/user_management.py.

Убраны дубли: «🎁 Рефералы» (топ пригласивших теперь в «📊 Статистика» →
«🎁 Реферальная статистика») и «🚫 Бан пользователей» из «🔒 Безопасность»
(тот же список, что «🚫 Заблокированные пользователи»).
«👑 Денежные админы» перенесены сюда из «💰 Финансы»: это права доступа (#116).
"""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import users_kb
from filters.admin import IsBotAdminFilter, IsMoneyAdmin
from data.config import ADMINS
from data.settings import get_settings, parse_user_ids
from services.settings_store import set_setting
from states.flows import AdminFlow, UserManagementFlow

from .common import blocked_users_text, cancel_kb, forms_router, record_setting_change

router = private_only(Router(name="private.admin.users"))

_ASK_USER = "Введите ID, @username или имя пользователя:"
_EDIT_MONEY_ADMINS = "money_admins_edit"


@router.message(F.text == "👥 Пользователи", IsBotAdminFilter(ADMINS))
async def users_menu(message: types.Message):
    await message.answer(
        "👥 <b>Пользователи</b>\n\n"
        "Откройте список или найдите конкретного пользователя.",
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.message(F.text == "📋 Все пользователи", IsBotAdminFilter(ADMINS))
async def users_list_alias(message: types.Message) -> None:
    from ..user_management import users_list_start

    await users_list_start(message)


@router.message(F.text == "💰 Изменить баланс", IsBotAdminFilter(ADMINS))
async def users_change_balance(message: types.Message, state: FSMContext) -> None:
    # #76: денежные операции — только денежные админы (#23); кнопки в профиле проверяют это ещё раз
    if not await IsMoneyAdmin()(message):
        await message.answer("⛔ Изменять баланс могут только денежные админы.", reply_markup=users_kb())
        return
    await state.set_state(UserManagementFlow.search_input)
    await state.update_data(search_purpose="balance")
    await message.answer(f"💰 Изменение баланса.\n{_ASK_USER}")


@router.message(F.text == "📨 Отправить сообщение", IsBotAdminFilter(ADMINS))
async def users_send_message(message: types.Message, state: FSMContext) -> None:
    # #77: после выбора пользователя сразу открывается ввод текста (user_management.py)
    await state.set_state(UserManagementFlow.search_input)
    await state.update_data(search_purpose="message")
    await message.answer(f"📨 Отправить сообщение.\n{_ASK_USER}")


@router.message(F.text == "🚫 Заблокированные пользователи", IsBotAdminFilter(ADMINS))
async def blocked_users_menu(message: types.Message):
    # #78: список с переходом в профиль, а не только число
    await message.answer(
        await blocked_users_text()
        + "\n\nЧтобы заблокировать, найдите пользователя через «🔍 Поиск пользователя».",
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


# ─────────────────────────────────────────────
# 👑 Администраторы и денежные админы
# ─────────────────────────────────────────────
def can_edit_money_admins(user_id: int) -> bool:
    """Пока денежных админов нет — назначает любой админ бота.
    Когда они есть — менять список может только денежный админ:
    иначе обычный админ мог бы сам дать себе доступ к деньгам."""
    current = get_settings().money_admins
    return not current or user_id in current


def _money_admin_lines(ids: tuple[int, ...] | list[int]) -> str:
    return "\n".join(f"• <code>{a}</code>" + ("" if a in ADMINS else " ⚠️ не в ADMINS") for a in ids)


def _admins_text() -> str:
    money = get_settings().money_admins
    admins = "\n".join(f"• <code>{a}</code>" + (" 💰" if a in money else "") for a in ADMINS) or "—"
    return (
        "👑 <b>Администраторы</b>\n\n"
        "<b>Админы бота</b> (задаются в .env, ADMINS):\n"
        f"{admins}\n\n"
        "💰 <b>Денежные админы</b> — одобряют выплаты и меняют балансы:\n"
        + (_money_admin_lines(money) or "Пока никто не назначен.")
    )


def _admins_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Изменить денежных админов", callback_data=_EDIT_MONEY_ADMINS)
    ]])


@router.message(F.text == "👑 Администраторы", IsBotAdminFilter(ADMINS))
async def admins_menu(message: types.Message):
    user = message.from_user
    if user is None:
        return
    if can_edit_money_admins(user.id):
        await message.answer(_admins_text(), parse_mode="HTML", reply_markup=_admins_kb())
        return
    await message.answer(
        _admins_text() + "\n\n🔒 Изменить список денежных админов может только денежный админ.",
        parse_mode="HTML",
        reply_markup=users_kb(),
    )


@router.callback_query(F.data == _EDIT_MONEY_ADMINS, IsBotAdminFilter(ADMINS))
async def money_admins_edit_start(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    if not can_edit_money_admins(call.from_user.id):
        await cast(Any, msg).answer("🔒 Изменить список может только денежный админ.", reply_markup=users_kb())
        return
    await state.set_state(AdminFlow.set_money_admins)
    await cast(Any, msg).answer(
        "Отправьте Telegram ID денежных админов через запятую — список заменится целиком.\n"
        "Пример: <code>111111111, 222222222</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )


def _money_admins_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="money_admins_confirm"),
        # «no» обрабатывает admin/menu.py::cancel_edit — форма закрывается
        InlineKeyboardButton(text="❌ Отмена", callback_data="no"),
    ]])


@forms_router.message(AdminFlow.set_money_admins, IsBotAdminFilter(ADMINS))
async def update_money_admins(message: types.Message, state: FSMContext):
    """Шаг 1: разбираем список и просим подтвердить — ошибка здесь лишает доступа к деньгам (#116)."""
    user = message.from_user
    if user is None:
        return
    # Проверяем ещё раз: список мог измениться, пока форма была открыта
    if not can_edit_money_admins(user.id):
        await message.answer("🔒 Изменить список может только денежный админ.", reply_markup=users_kb())
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
    await state.update_data(money_admins=list(ids))
    self_note = "" if user.id in ids else "\n\n⚠️ Вас нет в новом списке — вы потеряете доступ к выплатам."
    await message.answer(
        f"👑 Новый список денежных админов:\n{_money_admin_lines(ids)}{self_note}\n\nСохранить?",
        parse_mode="HTML",
        reply_markup=_money_admins_confirm_kb(),
    )


@router.callback_query(F.data == "money_admins_confirm", IsBotAdminFilter(ADMINS))
async def confirm_money_admins(call: types.CallbackQuery, state: FSMContext):
    """Шаг 2: сохраняем подтверждённый список."""
    msg = call.message
    if msg is None:
        return
    await call.answer()
    data = await state.get_data()
    ids = [int(a) for a in data.get("money_admins") or []]
    if await state.get_state() != AdminFlow.set_money_admins.state or not ids:
        await cast(Any, msg).answer("⚠️ Форма устарела. Откройте «👑 Администраторы» снова.",
                                    reply_markup=users_kb())
        await state.clear()
        return
    if not can_edit_money_admins(call.from_user.id):
        await cast(Any, msg).answer("🔒 Изменить список может только денежный админ.", reply_markup=users_kb())
        await state.clear()
        return
    old_value = await set_setting("money_admins", ids, call.from_user.id)
    await record_setting_change(call.from_user.id, "money_admins", old_value, ids)
    not_admins = [a for a in ids if a not in ADMINS]
    warning = (
        f"\n\n⚠️ {', '.join(map(str, not_admins))} нет в ADMINS: они не увидят админ-панель и заявки."
        if not_admins else ""
    )
    try:
        await cast(Any, msg).edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cast(Any, msg).answer("✅ Список обновлён.\n\n" + _admins_text() + warning,
                                parse_mode="HTML", reply_markup=users_kb())
    await state.clear()
