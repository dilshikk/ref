# /www/wwwroot/uz_ibot/handlers/private/withdrawals.py
import asyncio
import html
import random
from weakref import WeakValueDictionary

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from loguru import logger

from filters import private_only
from data.config import ADMINS, GROUP_ID, url_yaml
from loader import db
from states.flows import WithdrawalFlow
from services.activity import activity_service
from services.audit_log import audit_log_service

router = private_only(Router(name="private.withdrawals"))

# ✅ WeakValueDictionary — неактивные локи удаляются автоматически
user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def _cfg_int(key: str, default: int) -> int:
    """Число из url.yml. Админка может сохранить значение строкой ("5000"),
    и тогда сравнение баланса с ним падало с ошибкой — пользователь не получал ответа."""
    value = url_yaml.get(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"url.yml: {key}={value!r} не число, используется {default}")
        return default


def _mask_payment_info(value: str | None) -> str:
    """Карта/телефон для журналов: только последние 4 цифры.
    Полные реквизиты хранятся лишь в заявке на выплату — там они нужны админу."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return f"****{digits[-4:]}" if digits else "—"


# ======================================================
# 1. Вывести средства → проверка баланса
# ======================================================
async def _do_withdraw(user_id: int, reply_target: types.Message, state: FSMContext) -> None:
    """
    user_id      — Telegram ID пользователя (всегда явно передаём)
    reply_target — сообщение для ответа (может быть call.message)
    """
    lock = user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        user_locks[user_id] = lock

    if lock.locked():
        # Раньше повторное нажатие молча игнорировалось — казалось, что бот завис
        await reply_target.answer("⏳ Уже проверяю ваш баланс, подождите пару секунд.")
        return

    async with lock:
        try:
            record = await db.get_user_invited_count(user_id)

            min_balance = _cfg_int("min_balance", 0)
            max_withdraw = _cfg_int("max_withdraw", 9_999_999)
            # Баланс хранится в деньгах: смена цены за реферала его не пересчитывает
            balance = int(record["balance_sum"] or 0) if record else 0

            if await db.is_user_blocked(user_id):
                await reply_target.answer("⛔ Ваш аккаунт заблокирован. Вывод недоступен.")
                return

            if balance <= 0:
                await reply_target.answer(
                    "😕 <b>У вас пока нет доступных средств.</b>\n\n"
                    "💸 Приглашайте друзей и зарабатывайте.",
                    parse_mode="HTML",
                )
                return

            if balance < min_balance:
                await reply_target.answer(
                    f"⚠️ Недостаточно средств для вывода.\n\n"
                    f"💵 Баланс: <b>{balance}</b> сум\n"
                    f"📈 Минимум: <b>{min_balance}</b> сум\n"
                    f"➕ Не хватает: <b>{min_balance - balance}</b> сум",
                    parse_mode="HTML",
                )
                return

            # При одобрении списывается ровно эта сумма, остаток баланса сохраняется
            withdraw_amount = min(balance, max_withdraw)
            if withdraw_amount <= 0:
                await reply_target.answer("⚠️ Сумма к выводу слишком мала.")
                return

            if balance > max_withdraw:
                await reply_target.answer(
                    f"⚠️ Максимальный лимит вывода — <b>{max_withdraw}</b> сум.\n"
                    f"📈 Ваш баланс: <b>{balance}</b> сум.",
                    parse_mode="HTML",
                )

            markup = types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="✅ Продолжить", callback_data="withdraw_continue")
            ]])
            await state.update_data(balance=withdraw_amount)
            await reply_target.answer(
                f"💰 Сумма к выводу: <b>{withdraw_amount}</b> сум\n"
                "Нажмите <b>Продолжить</b>, чтобы перейти к оформлению. 👇",
                reply_markup=markup,
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error(f"Ошибка _do_withdraw для user={user_id}: {e}")
            try:
                await reply_target.answer("⚠️ Не удалось проверить баланс. Попробуйте чуть позже.")
            except Exception:
                pass


@router.message(F.text == "💸 Вывести средства")
async def start_withdraw_message(message: types.Message, state: FSMContext) -> None:
    # ✅ user_id берётся из message.from_user напрямую
    await _do_withdraw(message.from_user.id, message, state)


@router.callback_query(F.data == "go_withdraw")
async def start_withdraw_callback(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    # ✅ user_id берётся из call.from_user, не из call.message.from_user
    await _do_withdraw(call.from_user.id, call.message, state)


# ======================================================
# 2. Капча
# ======================================================
@router.callback_query(F.data == "withdraw_continue")
async def ask_captcha(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.delete()
    a, b = random.randint(1, 9), random.randint(1, 9)
    await state.update_data(captcha=a + b)
    await call.message.answer(
        f"🧩 Для безопасности ответьте: <b>{a} + {b}</b> = ?",
        parse_mode="HTML",
    )
    await state.set_state(WithdrawalFlow.CAPTCHA)


@router.message(WithdrawalFlow.CAPTCHA)
async def check_captcha(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) != data.get("captcha"):
        await message.reply("❌ Неверно! Попробуйте ещё раз.")
        return

    required_members = _cfg_int("group_member_count", 3)
    group_link: str = url_yaml.get("subcr_channel", "")

    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👥 Перейти в группу", url=group_link)],
        [types.InlineKeyboardButton(text="✅ Я добавил(а)", callback_data="check_group_members")],
    ])
    await message.answer(
        f"📢 Добавьте <b>{required_members}</b> участников в группу, "
        "затем нажмите «✅ Я добавил(а)».",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await state.set_state(WithdrawalFlow.CARD)


# ======================================================
# 3. Проверка добавленных участников
# ======================================================
@router.callback_query(F.data == "check_group_members", WithdrawalFlow.CARD)
async def check_group_members(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user_id = call.from_user.id
    # Та же группа, в которой бот считает добавленных (GROUP_ID из .env).
    # Раньше здесь читался subcr_chat_id из url.yml — при расхождении счётчик был всегда 0.
    group_id = GROUP_ID
    required_members = _cfg_int("group_member_count", 3)

    record = await db.get_user_add_count(group_id, user_id)
    added = int(record["add_user_count"] or 0) if record else 0

    if added < required_members:
        await call.message.answer(
            f"❌ Добавлено: <b>{added}</b> из <b>{required_members}</b>.\n"
            f"Нужно ещё <b>{required_members - added}</b> человек(а).\n\n"
            "ℹ️ После каждой выплаты счёт начинается заново.",
            parse_mode="HTML",
        )
        return

    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 На карту", callback_data="withdraw_card")],
        [types.InlineKeyboardButton(text="📱 На телефон", callback_data="withdraw_phone")],
    ])
    await call.message.answer(
        f"✅ Отлично! Добавлено <b>{added}</b> участников.\n\n"
        "💸 Выберите способ вывода:",
        parse_mode="HTML",
        reply_markup=markup,
    )
    # Кнопки «На карту»/«На телефон» принимаются только в этом состоянии
    await state.set_state(WithdrawalFlow.METHOD)


# ======================================================
# 4. Выбор способа вывода
# ======================================================
@router.callback_query(F.data == "withdraw_card", WithdrawalFlow.METHOD)
async def withdraw_card(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.update_data(withdraw_type="card")
    await call.message.answer("💳 Введите номер карты (16 цифр):")
    await state.set_state(WithdrawalFlow.CARD_NUMBER)


@router.callback_query(F.data == "withdraw_phone", WithdrawalFlow.METHOD)
async def withdraw_phone(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.update_data(withdraw_type="phone")
    await call.message.answer("📱 Введите номер телефона (+998XXXXXXXXX или 998XXXXXXXXX):")
    await state.set_state(WithdrawalFlow.CARD_NUMBER)


# ======================================================
# 5. Ввод реквизитов
# ======================================================
@router.message(WithdrawalFlow.CARD_NUMBER)
async def get_card_number(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = (message.text or "").strip().replace(" ", "")
    withdraw_type = data.get("withdraw_type")

    if withdraw_type == "card":
        if not raw.isdigit() or len(raw) != 16:
            await message.reply("⚠️ Введите корректный номер карты (16 цифр).")
            return
        await state.update_data(payment_info=raw)
        await message.answer("👤 Введите имя держателя карты (Имя Фамилия):")
        await state.set_state(WithdrawalFlow.CARD_HOLDER)
    else:
        is_with_plus = raw.startswith("+") and raw[1:].isdigit() and len(raw) == 13
        is_without_plus = raw.isdigit() and len(raw) == 12
        if not (is_with_plus or is_without_plus):
            await message.reply("⚠️ Введите телефон в формате +998XXXXXXXXX или 998XXXXXXXXX")
            return
        await state.update_data(payment_info=raw, card_holder="—")
        await _show_confirm(message, state)


@router.message(WithdrawalFlow.CARD_HOLDER)
async def get_card_holder(message: types.Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name.split()) < 2:
        await message.reply("⚠️ Введите полное имя (Имя Фамилия).")
        return
    await state.update_data(card_holder=name)
    await _show_confirm(message, state)


async def _show_confirm(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_withdraw"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_withdraw"),
    ]])
    # Введённое пользователем экранируем, иначе символы < > & ломают HTML
    await message.answer(
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"💰 Сумма: <b>{data.get('balance')}</b> сум\n"
        f"💳 Реквизиты: <code>{html.escape(str(data.get('payment_info')))}</code>\n"
        f"👤 Держатель: <b>{html.escape(str(data.get('card_holder', '—')))}</b>",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await state.set_state(WithdrawalFlow.END)


# ======================================================
# 6. Подтверждение → БД + уведомление админам
# ======================================================
@router.callback_query(F.data == "confirm_withdraw", WithdrawalFlow.END)
async def confirm_withdraw(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    data = await state.get_data()
    user_id = call.from_user.id

    existing = await db.get_pending_or_today_payout(user_id)
    if existing:
        await call.message.answer(
            "⚠️ Вы уже подавали заявку на вывод сегодня.\n"
            "Повторная заявка возможна через 24 часа."
        )
        await state.clear()
        return

    balance = data.get("balance")
    payment_info = data.get("payment_info")
    card_holder = data.get("card_holder", "—")

    if not balance or not payment_info:
        await call.message.answer("⚠️ Сессия устарела. Начните вывод заново.")
        await state.clear()
        return

    if await db.is_user_blocked(user_id):
        await call.message.answer("⛔ Ваш аккаунт заблокирован. Вывод недоступен.")
        await state.clear()
        return

    # Перепроверяем актуальный баланс: он мог измениться с начала оформления
    record = await db.get_user_invited_count(user_id)
    current_balance = int(record["balance_sum"] or 0) if record else 0
    if balance > current_balance:
        await call.message.answer(
            f"⚠️ Баланс изменился (сейчас <b>{current_balance}</b> сум). Начните вывод заново.",
            parse_mode="HTML",
        )
        await state.clear()
        return

    await db.add_payout_request(
        user_id=user_id,
        full_name=call.from_user.full_name,
        username=call.from_user.username or "—",
        card_number=payment_info,
        amount=balance,
    )
    # В журнал действий — только последние 4 цифры реквизитов
    await audit_log_service.record_user_action(
        actor_id=user_id,
        action="withdrawal_requested",
        entity_type="payout_request",
        entity_id=user_id,
        new_value={"amount": balance, "payment_info": _mask_payment_info(payment_info)},
        payload={"withdraw_type": data.get("withdraw_type")},
    )
    await activity_service.record(
        user_id=user_id,
        event="withdrawal_requested",
        chat_id=call.message.chat.id if call.message else None,
        session_id=call.id,
        payload={"amount": balance},
    )

    await call.message.edit_text(
        "✅ Заявка на вывод отправлена!\nОжидайте ответа администратора.",
        parse_mode="HTML",
    )
    await state.clear()

    # Имя и держатель экранируются: иначе Telegram отклонял сообщение и админ не узнавал о заявке
    for admin in ADMINS:
        try:
            await call.bot.send_message(
                admin,
                f"📢 <b>Новая заявка на вывод</b>\n\n"
                f"👤 <a href='tg://user?id={user_id}'>{html.escape(call.from_user.full_name)}</a>\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"💰 Сумма: <b>{balance}</b> сум\n"
                f"💳 Реквизиты: <code>{html.escape(str(payment_info))}</code>\n"
                f"👤 Держатель: {html.escape(str(card_holder))}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа {admin}: {e}")


# ======================================================
# 7. Отмена
# ======================================================
@router.callback_query(F.data == "cancel_withdraw")
async def cancel_withdraw(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.edit_text("❌ Действие отменено.")
