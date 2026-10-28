# /www/wwwroot/uz_ibot/handlers/private/user_management.py
import math

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from loguru import logger

from data.config import ADMINS, url_yaml
from filters import private_only
from filters.admin import IsBotAdminFilter
from loader import db, bot
from states.flows import UserManagementFlow
from services.audit_log import audit_log_service

router = private_only(Router(name="private.user_management"))

PAGE_SIZE = 10


# ======================================================
# 🔧 Вспомогательные функции
# ======================================================

def profile_text(p) -> str:
    invited_credit = url_yaml.get("invited_credit", 0)
    balance = p["current_count"] * invited_credit
    blocked = "🚫 Заблокирован" if p["is_blocked"] else "✅ Активен"
    username = f"@{p['username']}" if p["username"] else "—"
    created_at = p["created_at"].strftime("%d.%m.%Y %H:%M") if p.get("created_at") else "—"
    return (
        f"👤 <b>{p['full_name']}</b>\n"
        f"🆔 ID: <code>{p['telegram_id']}</code>\n"
        f"📎 Username: {username}\n"
        f"💰 Баланс: <b>{balance}</b> сум\n"
        f"👥 Приглашено всего: <b>{p['history_count']}</b>\n"
        f"📊 Текущий счётчик: <b>{p['current_count']}</b>\n"
        f"📅 Регистрация: <b>{created_at}</b>\n"
        f"🔒 Статус: {blocked}"
    )


def profile_kb(telegram_id: int, is_blocked: bool) -> types.InlineKeyboardMarkup:
    block_btn = (
        types.InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"umgmt_unblock_{telegram_id}")
        if is_blocked else
        types.InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"umgmt_block_{telegram_id}")
    )
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📜 История выплат", callback_data=f"umgmt_history_{telegram_id}")],
        [
            types.InlineKeyboardButton(text="➕ Начислить", callback_data=f"umgmt_addbal_{telegram_id}"),
            types.InlineKeyboardButton(text="➖ Обнулить", callback_data=f"umgmt_reset_{telegram_id}"),
        ],
        [block_btn],
        [types.InlineKeyboardButton(text="📩 Написать", callback_data=f"umgmt_msg_{telegram_id}")],
        [types.InlineKeyboardButton(text="🔙 К списку", callback_data="umgmt_list_0")],
    ])


def pagination_kb(page: int, total_pages: int) -> types.InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="◀️ Назад", callback_data=f"umgmt_list_{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"umgmt_list_{page + 1}"))
    return types.InlineKeyboardMarkup(inline_keyboard=[nav])


# ======================================================
# 1️⃣ Список пользователей с пагинацией
# ======================================================

@router.message(F.text == "📄 Список пользователей", IsBotAdminFilter(ADMINS))
async def users_list_start(message: types.Message) -> None:
    await show_users_page(message, page=0)


@router.callback_query(F.data.startswith("umgmt_list_"), IsBotAdminFilter(ADMINS))
async def users_list_page(call: types.CallbackQuery) -> None:
    await call.answer()  # ✅
    page = int(call.data.split("_")[2])
    await show_users_page(call, page=page)


async def show_users_page(event: types.Message | types.CallbackQuery, page: int) -> None:
    count_record = await db.get_users_count()
    total = count_record["count"] if count_record else 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    users = await db.get_users_paginated(offset=page * PAGE_SIZE, limit=PAGE_SIZE)

    if not users:
        text = "😔 Пользователей пока нет."
        kb = None
    else:
        lines = []
        for u in users:
            username = f"@{u['username']}" if u["username"] else "—"
            blocked = " 🚫" if u["is_blocked"] else ""
            lines.append(
                f"• <a href='tg://user?id={u['telegram_id']}'>{u['full_name']}</a> "
                f"({username}){blocked} — /uid{u['telegram_id']}"
            )
        text = (
            f"👥 <b>Пользователи</b> [{page + 1}/{total_pages}] (всего: {total})\n\n"
            + "\n".join(lines)
            + "\n\n🔍 Нажмите /uid<b>ID</b> чтобы открыть профиль\nИли используйте поиск 👇"
        )
        kb = pagination_kb(page, total_pages)

    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ======================================================
# 2️⃣ Поиск пользователя
# ======================================================

@router.message(F.text == "🔍 Поиск пользователя", IsBotAdminFilter(ADMINS))
async def search_start(message: types.Message, state: FSMContext) -> None:
    await message.answer("🔍 Введите ID, @username или имя пользователя:")
    await state.set_state(UserManagementFlow.search_input)


@router.message(UserManagementFlow.search_input, IsBotAdminFilter(ADMINS))
async def search_process(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip().lstrip("@")
    await state.clear()

    users = await db.search_users(query)

    if not users:
        await message.answer("❌ Пользователь не найден.")
        return

    if len(users) == 1:
        await show_profile(message, users[0]["telegram_id"])
        return

    buttons = [
        [types.InlineKeyboardButton(
            text=f"{u['full_name']} (@{u['username']}" if u["username"] else f"{u['full_name']} (без username)",
            callback_data=f"umgmt_profile_{u['telegram_id']}"
        )]
        for u in users
    ]
    await message.answer(
        f"🔍 Найдено {len(users)} пользователей. Выберите:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ======================================================
# 3️⃣ Профиль пользователя
# ======================================================

@router.message(F.text.regexp(r"^/uid\d+$"), IsBotAdminFilter(ADMINS))
async def profile_by_command(message: types.Message) -> None:
    telegram_id = int(message.text[4:])
    await show_profile(message, telegram_id)


@router.callback_query(F.data.startswith("umgmt_profile_"), IsBotAdminFilter(ADMINS))
async def profile_by_callback(call: types.CallbackQuery) -> None:
    await call.answer()  # ✅
    telegram_id = int(call.data.split("_")[2])
    await show_profile(call, telegram_id)


async def show_profile(event: types.Message | types.CallbackQuery, telegram_id: int) -> None:
    profile = await db.get_user_profile(telegram_id)
    last_activity = await db.get_last_user_activity_at(telegram_id)

    if not profile:
        text = f"❌ Пользователь <code>{telegram_id}</code> не найден в базе."
        kb = None
    else:
        text = profile_text(profile)
        text += "\n" + f"🕒 Последняя активность: <b>{last_activity['created_at'].strftime('%d.%m.%Y %H:%M') if last_activity else '—'}</b>"
        kb = profile_kb(profile["telegram_id"], profile["is_blocked"])

    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ======================================================
# 4️⃣ История выплат
# ======================================================

@router.callback_query(F.data.startswith("umgmt_history_"), IsBotAdminFilter(ADMINS))
async def show_payout_history(call: types.CallbackQuery) -> None:
    await call.answer()  # ✅
    telegram_id = int(call.data.split("_")[2])
    payouts = await db.get_user_payout_history(telegram_id)

    if not payouts:
        text = f"📜 У пользователя <code>{telegram_id}</code> нет истории выплат."
    else:
        lines = [
            f"💳 <code>{p['card_number']}</code> — <b>{p['amount']}</b> сум\n"
            f"   Статус: {p['status']} | {p['created_at'].strftime('%d.%m.%Y %H:%M')}"
            for p in payouts
        ]
        text = f"📜 <b>История выплат</b> пользователя <code>{telegram_id}</code>:\n\n" + "\n\n".join(lines)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🔙 Профиль", callback_data=f"umgmt_profile_{telegram_id}")
    ]])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ======================================================
# 5️⃣ Начислить баланс
# ======================================================

@router.callback_query(F.data.startswith("umgmt_addbal_"), IsBotAdminFilter(ADMINS))
async def add_balance_start(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅
    telegram_id = int(call.data.split("_")[2])
    await state.update_data(target_user_id=telegram_id)
    await state.set_state(UserManagementFlow.add_balance_input)
    await call.message.answer(
        f"➕ Введите количество приглашений для начисления пользователю <code>{telegram_id}</code>:\n"
        f"(например: <code>10</code> = +{10 * url_yaml.get('invited_credit', 0)} сум)",
        parse_mode="HTML",
    )


@router.message(UserManagementFlow.add_balance_input, IsBotAdminFilter(ADMINS))
async def add_balance_process(message: types.Message, state: FSMContext) -> None:
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите целое положительное число.")
        return

    amount = int(message.text)
    data = await state.get_data()
    telegram_id = data["target_user_id"]
    await state.clear()

    before = await db.get_user_invited_count(telegram_id)
    before_current = before["current_count"] if before else 0
    before_history = before["history_count"] if before else 0

    await db.add_balance(telegram_id, amount)
    invited_credit: int = url_yaml.get("invited_credit", 0)
    after_current = before_current + amount
    after_history = before_history + amount
    await audit_log_service.record_admin_action(
        actor_id=message.from_user.id,
        action="admin_balance_changed",
        entity_type="user",
        entity_id=telegram_id,
        old_value={"current_count": before_current, "history_count": before_history},
        new_value={"current_count": after_current, "history_count": after_history},
        payload={"amount": amount, "invited_credit": invited_credit},
    )

    await message.answer(
        f"✅ Пользователю <code>{telegram_id}</code> начислено "
        f"<b>{amount}</b> приглашений (+{amount * invited_credit} сум).",
        parse_mode="HTML",
    )
    try:
        await bot.send_message(
            telegram_id,
            f"🎁 Администратор начислил вам бонус: <b>+{amount * invited_credit}</b> сум!",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ======================================================
# 6️⃣ Обнулить баланс
# ======================================================

@router.callback_query(F.data.startswith("umgmt_reset_"), IsBotAdminFilter(ADMINS))
async def reset_balance_confirm(call: types.CallbackQuery) -> None:
    await call.answer()  # ✅
    telegram_id = int(call.data.split("_")[2])
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Да, обнулить", callback_data=f"umgmt_resetok_{telegram_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"umgmt_profile_{telegram_id}"),
    ]])
    await call.message.edit_text(
        f"⚠️ Обнулить текущий баланс пользователя <code>{telegram_id}</code>?\n"
        "(история сохранится, только текущий счётчик → 0)",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("umgmt_resetok_"), IsBotAdminFilter(ADMINS))
async def reset_balance_execute(call: types.CallbackQuery) -> None:
    telegram_id = int(call.data.split("_")[2])
    before = await db.get_user_invited_count(telegram_id)
    await db.update_invite_current_count_set_0(telegram_id)
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="admin_balance_reset",
        entity_type="user",
        entity_id=telegram_id,
        old_value={"current_count": before["current_count"] if before else 0},
        new_value={"current_count": 0},
        payload={"reason": "admin_reset"},
    )
    await call.answer("✅ Баланс обнулён.", show_alert=True)
    await show_profile(call, telegram_id)


# ======================================================
# 7️⃣ Заблокировать / Разблокировать
# ======================================================

@router.callback_query(F.data.startswith("umgmt_block_"), IsBotAdminFilter(ADMINS))
async def block_user(call: types.CallbackQuery) -> None:
    telegram_id = int(call.data.split("_")[2])
    before = await db.select_user(telegram_id=telegram_id)
    await db.block_user(telegram_id)
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="user_blocked",
        entity_type="user",
        entity_id=telegram_id,
        old_value={"is_blocked": bool(before["is_blocked"]) if before else False},
        new_value={"is_blocked": True},
    )
    await call.answer("🚫 Пользователь заблокирован.", show_alert=True)
    try:
        await bot.send_message(telegram_id, "🚫 Ваш аккаунт был заблокирован администратором.")
    except Exception:
        pass
    await show_profile(call, telegram_id)


@router.callback_query(F.data.startswith("umgmt_unblock_"), IsBotAdminFilter(ADMINS))
async def unblock_user(call: types.CallbackQuery) -> None:
    telegram_id = int(call.data.split("_")[2])
    before = await db.select_user(telegram_id=telegram_id)
    await db.unblock_user(telegram_id)
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="user_unblocked",
        entity_type="user",
        entity_id=telegram_id,
        old_value={"is_blocked": bool(before["is_blocked"]) if before else True},
        new_value={"is_blocked": False},
    )
    await call.answer("✅ Пользователь разблокирован.", show_alert=True)
    try:
        await bot.send_message(telegram_id, "✅ Ваш аккаунт был разблокирован администратором.")
    except Exception:
        pass
    await show_profile(call, telegram_id)


# ======================================================
# 8️⃣ Написать пользователю
# ======================================================

@router.callback_query(F.data.startswith("umgmt_msg_"), IsBotAdminFilter(ADMINS))
async def send_message_start(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅
    telegram_id = int(call.data.split("_")[2])
    await state.update_data(target_user_id=telegram_id)
    await state.set_state(UserManagementFlow.send_message_input)
    await call.message.answer(
        f"📩 Введите сообщение для пользователя <code>{telegram_id}</code>:\n"
        "(поддерживается HTML-форматирование)",
        parse_mode="HTML",
    )


@router.message(UserManagementFlow.send_message_input, IsBotAdminFilter(ADMINS))
async def send_message_process(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    telegram_id = data["target_user_id"]
    await state.clear()

    try:
        await bot.send_message(
            telegram_id,
            f"📬 <b>Сообщение от администратора:</b>\n\n{message.text}",
            parse_mode="HTML",
        )
        await message.answer(
            f"✅ Сообщение отправлено пользователю <code>{telegram_id}</code>.",
            parse_mode="HTML",
        )
        logger.success(f"📩 Сообщение отправлено пользователю {telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения {telegram_id}: {e}")
        await message.answer(
            f"❌ Не удалось отправить сообщение: <code>{e}</code>",
            parse_mode="HTML",
        )


# ======================================================
# 🔇 Заглушка для кнопки номера страницы
# ======================================================

@router.callback_query(F.data == "noop")
async def noop(call: types.CallbackQuery) -> None:
    await call.answer()
