# handlers/private/withdrawals.py
import asyncio
import html
import time
from weakref import WeakValueDictionary

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from loguru import logger

from filters import private_only
from data.config import ADMINS, GROUP_ID, MONEY_ADMINS
from data.settings import get_settings
from loader import db
from states.flows import WithdrawalFlow
from services.activity import activity_service
from services.audit_log import audit_log_service
from services.group_adds import locked_explanation, qualification_enabled, qualify_pending, unverified_referral_sum
from utils import captcha, payment_details
from utils.payout_math import withdrawable

router = private_only(Router(name="private.withdrawals"))

# ✅ WeakValueDictionary — неактивные локи удаляются автоматически
user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

ALREADY_REQUESTED_TEXT = (
    "⚠️ Вы уже подавали заявку на вывод сегодня.\n"
    "Повторная заявка возможна через 24 часа."
)


def _mask_payment_info(value: str | None) -> str:
    """Карта/телефон для журналов: только последние 4 цифры."""
    return payment_details.mask(value)


def _lock_text(seconds: int) -> str:
    minutes = max(1, (seconds + 59) // 60)
    return (
        "⛔ Слишком много неверных ответов на проверку.\n"
        f"Вывод временно недоступен, попробуйте через <b>{minutes}</b> мин."
    )


async def _captcha_lock_seconds(user_id: int) -> int:
    try:
        return await db.get_captcha_lock_seconds(user_id)
    except Exception as e:
        # Не удалось проверить — не пускаем к выводу, а не наоборот
        logger.error(f"Не удалось проверить блокировку капчи user={user_id}: {e}")
        return 60


async def _withdrawable_now(user_id: int) -> tuple[int, int, int]:
    """
    (баланс, на проверке, можно вывести) (#46).
    Перед расчётом проверяем добавленных: прошедшие проверку сразу открывают деньги.
    """
    try:
        await qualify_pending(GROUP_ID, user_id)
    except Exception as e:
        logger.error(f"Ошибка проверки добавленных inviter={user_id}: {e}")
    record = await db.get_user_invited_count(user_id)
    # Баланс хранится в деньгах: смена цены за реферала его не пересчитывает
    balance = int(record["balance_sum"] or 0) if record else 0
    locked = await unverified_referral_sum(user_id)
    return balance, locked, withdrawable(balance, locked, 0)


def _locked_note(locked: int) -> str:
    if locked <= 0:
        return ""
    explanation = locked_explanation()
    return f"\n🔒 На проверке: <b>{locked}</b> сум." + (f"\n<i>{explanation}</i>" if explanation else "")


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
            # Блокировка после неверных ответов капчи хранится в БД: /start её не сбрасывает (#18)
            locked_for = await _captcha_lock_seconds(user_id)
            if locked_for > 0:
                await reply_target.answer(_lock_text(locked_for), parse_mode="HTML")
                return

            if await db.is_user_blocked(user_id):
                await reply_target.answer("⛔ Ваш аккаунт заблокирован. Вывод недоступен.")
                return

            # get_settings(): строка "5000" или мусор в url.yml не роняют сравнение (#22)
            settings = get_settings()
            min_balance = settings.min_balance
            max_withdraw = settings.max_withdraw
            # Деньги за непроверенных приглашённых не выводятся (#46)
            _, locked, balance = await _withdrawable_now(user_id)
            locked_note = _locked_note(locked)

            if balance <= 0:
                await reply_target.answer(
                    "😕 <b>У вас пока нет доступных средств.</b>\n\n"
                    "💸 Приглашайте друзей и зарабатывайте." + locked_note,
                    parse_mode="HTML",
                )
                return

            if balance < min_balance:
                await reply_target.answer(
                    f"⚠️ Недостаточно средств для вывода.\n\n"
                    f"💵 Доступно: <b>{balance}</b> сум\n"
                    f"📈 Минимум: <b>{min_balance}</b> сум\n"
                    f"➕ Не хватает: <b>{min_balance - balance}</b> сум" + locked_note,
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
                    f"📈 Доступно: <b>{balance}</b> сум.",
                    parse_mode="HTML",
                )

            markup = types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="✅ Продолжить", callback_data="withdraw_continue")
            ]])
            await state.update_data(balance=withdraw_amount)
            await reply_target.answer(
                f"💰 Сумма к выводу: <b>{withdraw_amount}</b> сум{locked_note}\n"
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
# 2. Капча (#18)
# ======================================================
async def _send_captcha(target: types.Message, state: FSMContext, prefix: str = "") -> None:
    """Новый пример: после каждой ошибки ответ меняется, перебор не работает."""
    c = captcha.generate()
    await state.update_data(captcha=c.answer, captcha_issued_at=time.time())
    await target.answer(
        f"{prefix}🧩 Для безопасности решите пример и отправьте ответ числом:\n\n"
        f"<b>{c.question} = ?</b>",
        parse_mode="HTML",
    )
    await state.set_state(WithdrawalFlow.CAPTCHA)


@router.callback_query(F.data == "withdraw_continue")
async def ask_captcha(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        await call.message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    locked_for = await _captcha_lock_seconds(call.from_user.id)
    if locked_for > 0:
        await state.clear()
        await call.message.answer(_lock_text(locked_for), parse_mode="HTML")
        return
    await _send_captcha(call.message, state)


@router.message(WithdrawalFlow.CAPTCHA)
async def check_captcha(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = await state.get_data()

    answer = captcha.parse_answer(message.text)
    if answer is None:
        # Не число — попытку не засчитываем
        await message.reply("✍️ Отправьте ответ числом, например: 42")
        return

    if captcha.is_expired(data.get("captcha_issued_at"), time.time()):
        await _send_captcha(message, state, prefix="⌛ Время на ответ вышло. Вот новый пример.\n\n")
        return

    if answer != data.get("captcha"):
        try:
            attempts, locked_for = await db.register_captcha_failure(
                user_id,
                max_attempts=captcha.MAX_ATTEMPTS,
                lock_seconds=captcha.LOCK_SECONDS,
                window_seconds=captcha.FAIL_WINDOW_SECONDS,
            )
        except Exception as e:
            logger.error(f"Не удалось записать ошибку капчи user={user_id}: {e}")
            await state.clear()
            await message.answer("⚠️ Не удалось проверить ответ. Начните вывод заново чуть позже.")
            return

        if locked_for > 0:
            await state.clear()
            await audit_log_service.record_user_action(
                actor_id=user_id,
                action="withdraw_captcha_locked",
                entity_type="user",
                entity_id=user_id,
                payload={"attempts": attempts, "lock_seconds": locked_for},
            )
            logger.warning(f"🚨 Капча: user={user_id} заблокирован на {locked_for} сек после {attempts} ошибок")
            await message.answer(_lock_text(locked_for), parse_mode="HTML")
            return

        left = captcha.MAX_ATTEMPTS - attempts
        await _send_captcha(message, state, prefix=f"❌ Неверно. Осталось попыток: <b>{left}</b>.\n\n")
        return

    try:
        await db.reset_captcha_failures(user_id)
    except Exception as e:
        logger.warning(f"Не удалось сбросить ошибки капчи user={user_id}: {e}")
    await state.update_data(captcha=None, captcha_issued_at=None)

    settings = get_settings()
    required_members = settings.group_member_count

    rows: list[list[types.InlineKeyboardButton]] = []
    # Кнопка-ссылка с пустым или не-http адресом роняла отправку сообщения:
    # без ссылки на группу показываем только «Я добавил(а)»
    if settings.group_link.startswith(("http://", "https://", "tg://")):
        rows.append([types.InlineKeyboardButton(text="👥 Перейти в группу", url=settings.group_link)])
    else:
        logger.warning("url.yml: subcr_channel не задан или не ссылка — кнопка группы скрыта")
    rows.append([types.InlineKeyboardButton(text="✅ Я добавил(а)", callback_data="check_group_members")])

    note = ""
    if qualification_enabled():
        note = (
            f"\n\nℹ️ Участник засчитывается, когда пробудет в группе не меньше "
            f"<b>{settings.add_min_hours}</b> ч. и проявит активность "
            "(напишет в группу или запустит бота)."
        )
    await message.answer(
        f"📢 Добавьте <b>{required_members}</b> участников в группу, "
        f"затем нажмите «✅ Я добавил(а)».{note}",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
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
    settings = get_settings()
    required_members = settings.group_member_count

    # Сначала засчитываем добавленных, прошедших проверку (#18)
    waiting = 0
    try:
        report = await qualify_pending(group_id, user_id)
        waiting = report.waiting
    except Exception as e:
        logger.error(f"Ошибка проверки добавленных inviter={user_id}: {e}")

    record = await db.get_user_add_count(group_id, user_id)
    added = int(record["add_user_count"] or 0) if record else 0

    if added < required_members:
        waiting_note = (
            f"\n⏳ Ещё <b>{waiting}</b> на проверке: они будут засчитаны, когда пробудут в группе "
            f"{settings.add_min_hours} ч. и проявят активность."
            if waiting
            else ""
        )
        await call.message.answer(
            f"❌ Засчитано: <b>{added}</b> из <b>{required_members}</b>.\n"
            f"Нужно ещё <b>{required_members - added}</b> человек(а).{waiting_note}\n\n"
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
    raw = message.text or ""
    withdraw_type = data.get("withdraw_type")

    if withdraw_type == "card":
        card = payment_details.normalize_card(raw)
        if card is None:
            await message.reply(
                "⚠️ Номер карты неверный. Проверьте 16 цифр — возможно, в номере опечатка."
            )
            return
        await state.update_data(payment_info=card)
        await message.answer("👤 Введите имя держателя карты (Имя Фамилия):")
        await state.set_state(WithdrawalFlow.CARD_HOLDER)
    else:
        phone = payment_details.normalize_uz_phone(raw)
        if phone is None:
            await message.reply(
                "⚠️ Введите мобильный номер Узбекистана: +998XXXXXXXXX "
                "(код оператора: 90, 91, 93, 94, 95, 97, 98, 99, 33, 88, 77, 50, 55, 20)."
            )
            return
        await state.update_data(payment_info=phone, card_holder="—")
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


def _details_for(admin_id: int, payment_info: str) -> str:
    """Полные реквизиты нужны только тому, кто платит (MONEY_ADMINS); остальным — маска."""
    return str(payment_info) if admin_id in MONEY_ADMINS else payment_details.mask(payment_info)


# ======================================================
# 6. Подтверждение → БД + уведомление админам
# ======================================================
@router.callback_query(F.data == "confirm_withdraw", WithdrawalFlow.END)
async def confirm_withdraw(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    data = await state.get_data()
    user_id = call.from_user.id

    # Быстрая проверка, чтобы не гонять расчёт баланса зря.
    # Окончательная — в add_payout_request, в одном SQL со вставкой (#47)
    existing = await db.get_pending_or_today_payout(user_id)
    if existing:
        await call.message.answer(ALREADY_REQUESTED_TEXT)
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

    # Перепроверяем доступную сумму: баланс и деньги «на проверке» могли измениться (#46)
    _, _, current_available = await _withdrawable_now(user_id)
    if balance > current_available:
        await call.message.answer(
            f"⚠️ Доступная сумма изменилась (сейчас <b>{current_available}</b> сум). Начните вывод заново.",
            parse_mode="HTML",
        )
        await state.clear()
        return

    # Норму «добавил N участников» тоже перепроверяем: форма могла пролежать в FSM
    # до 7 дней, и за это время добавленные могли выйти из группы
    required_members = get_settings().group_member_count
    if required_members > 0:
        added_row = await db.get_user_add_count(GROUP_ID, user_id)
        added = int(added_row["add_user_count"] or 0) if added_row else 0
        if added < required_members:
            await call.message.answer(
                f"⚠️ Сейчас добавлено <b>{added}</b> из <b>{required_members}</b> участников "
                "(кто-то вышел из группы). Добавьте недостающих и начните вывод заново.",
                parse_mode="HTML",
            )
            await state.clear()
            return

    request_id = await db.add_payout_request(
        user_id=user_id,
        full_name=call.from_user.full_name,
        username=call.from_user.username or "—",
        card_number=payment_info,
        amount=balance,
    )
    if request_id is None:
        # Заявку успели создать параллельно (второй экземпляр бота, двойное нажатие) (#47)
        logger.warning(f"Повторная заявка на вывод отклонена базой: user={user_id}")
        await call.message.answer(ALREADY_REQUESTED_TEXT)
        await state.clear()
        return

    # В журнал действий — только последние 4 цифры реквизитов
    await audit_log_service.record_user_action(
        actor_id=user_id,
        action="withdrawal_requested",
        entity_type="payout_request",
        entity_id=request_id,
        new_value={"amount": balance, "payment_info": _mask_payment_info(payment_info)},
        payload={"withdraw_type": data.get("withdraw_type"), "user_id": user_id},
    )
    await activity_service.record(
        user_id=user_id,
        event="withdrawal_requested",
        chat_id=call.message.chat.id if call.message else None,
        session_id=call.id,
        payload={"amount": balance, "request_id": request_id},
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
                f"📢 <b>Новая заявка на вывод</b> #{request_id}\n\n"
                f"👤 <a href='tg://user?id={user_id}'>{html.escape(call.from_user.full_name)}</a>\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"💰 Сумма: <b>{balance}</b> сум\n"
                f"💳 Реквизиты: <code>{html.escape(_details_for(admin, payment_info))}</code>\n"
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
