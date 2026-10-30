# handlers/private/withdrawals.py
import asyncio
import html
import time
from weakref import WeakValueDictionary

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from loguru import logger

from filters import private_only
from data.config import ADMINS
from data.settings import get_settings
from filters.admin import is_money_admin
from loader import db
from states.flows import WithdrawalFlow
from services.activity import activity_service
from services.audit_log import audit_log_service
from services.group_adds import locked_explanation, qualification_enabled, qualify_pending, unverified_referral_sum
from services.settings_store import GROUP_NOT_SET_TEXT
from utils import captcha, payment_details
from utils.payout_math import withdrawable

router = private_only(Router(name="private.withdrawals"))

# ✅ WeakValueDictionary — неактивные локи удаляются автоматически
user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

ALREADY_REQUESTED_TEXT = (
    "⚠️ Вы уже подавали заявку на вывод сегодня.\n"
    "Повторная заявка возможна через 24 часа."
)

# Финансовый админ не ограничен «раз в сутки», но одна заявка в ожидании — всё равно одна
ADMIN_PENDING_TEXT = (
    "⚠️ У вас уже есть заявка на вывод в ожидании.\n"
    "Одобрите или отклоните её, затем подайте новую."
)

ADMIN_SKIP_MEMBERS_NOTE = "👑 Вы финансовый администратор: проверка добавленных участников пропущена.\n\n"

# Вывод на телефон: имя держателя не спрашивается
NO_HOLDER = "—"

# Подсказки для ввода реквизитов по способу вывода (ключи — payment_details.SAVED_KINDS)
DETAILS_PROMPTS = {
    "card": "💳 Введите номер карты (16 цифр):",
    "phone": "📱 Введите номер телефона (+998XXXXXXXXX или 998XXXXXXXXX):",
}
HOLDER_PROMPT = "👤 Введите имя держателя карты (Имя Фамилия):"


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
    group_id = get_settings().group_id
    try:
        if group_id is not None:
            await qualify_pending(group_id, user_id)
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


async def _ask_method(target: types.Message, state: FSMContext, header: str) -> None:
    """Выбор способа вывода: «На карту» / «На телефон»."""
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 На карту", callback_data="withdraw_card")],
        [types.InlineKeyboardButton(text="📱 На телефон", callback_data="withdraw_phone")],
    ])
    await target.answer(
        f"{header}💸 Выберите способ вывода:",
        parse_mode="HTML",
        reply_markup=markup,
    )
    # Кнопки «На карту»/«На телефон» принимаются только в этом состоянии
    await state.set_state(WithdrawalFlow.METHOD)


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

            # get_settings(): строка "5000" или мусор в настройках не роняют сравнение (#22)
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
        f"<b>{c.question} = ?</b>\n\n"
        f"⏱ На ответ даётся <b>{captcha.time_limit_text()}</b>",
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

    # Финансовому админу норма «добавь N участников» не нужна — сразу к способу вывода
    if is_money_admin(user_id):
        await _ask_method(message, state, ADMIN_SKIP_MEMBERS_NOTE)
        return

    settings = get_settings()
    required_members = settings.group_member_count

    rows: list[list[types.InlineKeyboardButton]] = []
    # Кнопка-ссылка с пустым или не-http адресом роняла отправку сообщения:
    # без ссылки на группу показываем только «Я добавил(а)»
    if settings.group_link.startswith(("http://", "https://", "tg://")):
        rows.append([types.InlineKeyboardButton(text="👥 Перейти в группу", url=settings.group_link)])
    else:
        logger.warning("Ссылка группы (subcr_channel) не задана или не ссылка — кнопка группы скрыта")
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
    if is_money_admin(user_id):
        await _ask_method(call.message, state, ADMIN_SKIP_MEMBERS_NOTE)
        return

    # Та же группа, в которой бот считает добавленных: «🆔 ID группы» в админке (#116).
    settings = get_settings()
    group_id = settings.group_id
    if group_id is None:
        await call.message.answer(GROUP_NOT_SET_TEXT)
        return
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

    await _ask_method(call.message, state, f"✅ Отлично! Добавлено <b>{added}</b> участников.\n\n")


# ======================================================
# 4. Выбор способа вывода → сохранённые реквизиты или ввод
# ======================================================
async def _load_saved(user_id: int, kind: str) -> dict[str, str | None] | None:
    """Сохранённые реквизиты или None. Ошибка базы не ломает вывод: просто спросим реквизиты."""
    try:
        row = await db.get_saved_payment_details(user_id, kind)
    except Exception as e:
        logger.warning(f"Не удалось прочитать сохранённые реквизиты user={user_id}: {e}")
        return None
    if not row:
        return None
    return payment_details.open_saved(kind, row.get("details"), row.get("holder"))


async def _remember_details(user_id: int, kind: object, details: str, holder: str | None) -> bool:
    """Запоминаем реквизиты после заявки. Ошибка не отменяет уже созданную заявку."""
    if kind not in DETAILS_PROMPTS:
        return False
    try:
        await db.save_payment_details(user_id, str(kind), details, holder if kind == "card" else None)
        return True
    except Exception as e:
        logger.warning(f"Не удалось сохранить реквизиты user={user_id}: {e}")
        return False


async def _drop_buttons(call: types.CallbackQuery) -> None:
    """Убираем кнопки выбора, чтобы их нельзя было нажать повторно."""
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug(f"Не удалось убрать кнопки: {e}")


async def _ask_details(target: types.Message, state: FSMContext, kind: str) -> None:
    await target.answer(DETAILS_PROMPTS[kind])
    await state.set_state(WithdrawalFlow.CARD_NUMBER)


async def _start_details(call: types.CallbackQuery, state: FSMContext, kind: str) -> None:
    """Если реквизиты этого способа сохранены — предлагаем их, иначе спрашиваем, как раньше."""
    await state.update_data(withdraw_type=kind)
    saved = await _load_saved(call.from_user.id, kind)
    if saved is None or not saved["details"]:
        await _ask_details(call.message, state, kind)
        return
    label = payment_details.saved_label(saved["details"], saved["holder"])
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"✅ Использовать {label}", callback_data="saved_use")],
        [types.InlineKeyboardButton(text="✏️ Ввести новые", callback_data="saved_new")],
        [types.InlineKeyboardButton(text="🗑 Удалить мои реквизиты", callback_data="saved_delete")],
    ])
    await call.message.answer(
        f"💾 У вас сохранены реквизиты: <b>{html.escape(label)}</b>\nИспользовать их?",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await state.set_state(WithdrawalFlow.SAVED_CHOICE)


@router.callback_query(F.data == "withdraw_card", WithdrawalFlow.METHOD)
async def withdraw_card(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start_details(call, state, "card")


@router.callback_query(F.data == "withdraw_phone", WithdrawalFlow.METHOD)
async def withdraw_phone(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start_details(call, state, "phone")


@router.callback_query(F.data == "saved_use", WithdrawalFlow.SAVED_CHOICE)
async def use_saved_details(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _drop_buttons(call)
    kind = (await state.get_data()).get("withdraw_type")
    if kind not in DETAILS_PROMPTS:
        await state.clear()
        await call.message.answer("⚠️ Сессия устарела. Начните вывод заново.")
        return

    # Читаем заново: реквизиты могли удалить, пока сообщение висело
    saved = await _load_saved(call.from_user.id, kind)
    if saved is None or not saved["details"]:
        await call.message.answer("⚠️ Сохранённые реквизиты недоступны. Введите их заново.")
        await _ask_details(call.message, state, kind)
        return

    if kind == "card":
        await state.update_data(payment_info=saved["details"])
        if not saved["holder"]:
            # Карта сохранена без имени — спрашиваем только имя
            await call.message.answer(HOLDER_PROMPT)
            await state.set_state(WithdrawalFlow.CARD_HOLDER)
            return
        await state.update_data(card_holder=saved["holder"])
    else:
        await state.update_data(payment_info=saved["details"], card_holder=NO_HOLDER)
    await _show_confirm(call.message, state)


@router.callback_query(F.data == "saved_new", WithdrawalFlow.SAVED_CHOICE)
async def enter_new_details(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _drop_buttons(call)
    kind = (await state.get_data()).get("withdraw_type")
    if kind not in DETAILS_PROMPTS:
        await state.clear()
        await call.message.answer("⚠️ Сессия устарела. Начните вывод заново.")
        return
    await _ask_details(call.message, state, kind)


@router.callback_query(F.data == "saved_delete")
async def delete_saved_details(call: types.CallbackQuery, state: FSMContext) -> None:
    """Удаляет все сохранённые реквизиты пользователя (и карту, и телефон)."""
    await call.answer()
    await _drop_buttons(call)
    try:
        deleted = await db.delete_saved_payment_details(call.from_user.id)
    except Exception as e:
        logger.error(f"Не удалось удалить реквизиты user={call.from_user.id}: {e}")
        await call.message.answer("⚠️ Не удалось удалить реквизиты. Попробуйте чуть позже.")
        return
    await call.message.answer(
        "🗑 Ваши сохранённые реквизиты удалены." if deleted else "ℹ️ Сохранённых реквизитов нет."
    )
    await audit_log_service.record_user_action(
        actor_id=call.from_user.id,
        action="saved_payment_details_deleted",
        entity_type="user",
        entity_id=call.from_user.id,
        payload={"deleted": deleted},
    )
    # Если пользователь был в середине вывода — продолжаем с ввода новых реквизитов
    if await state.get_state() == WithdrawalFlow.SAVED_CHOICE.state:
        kind = (await state.get_data()).get("withdraw_type")
        if kind in DETAILS_PROMPTS:
            await _ask_details(call.message, state, kind)


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
        await message.answer(HOLDER_PROMPT)
        await state.set_state(WithdrawalFlow.CARD_HOLDER)
    else:
        phone = payment_details.normalize_uz_phone(raw)
        if phone is None:
            await message.reply(
                "⚠️ Введите мобильный номер Узбекистана: +998XXXXXXXXX "
                "(код оператора: 90, 91, 93, 94, 95, 97, 98, 99, 33, 88, 77, 50, 55, 20)."
            )
            return
        await state.update_data(payment_info=phone, card_holder=NO_HOLDER)
        await _show_confirm(message, state)


@router.message(WithdrawalFlow.CARD_HOLDER)
async def get_card_holder(message: types.Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name.split()) < 2:
        await message.reply("⚠️ Введите полное имя (Имя Фамилия).")
        return
    # Имя сохраняется в заявке (колонка card_holder, до 255 символов достаточно)
    await state.update_data(card_holder=name[:255])
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
        f"👤 Держатель: <b>{html.escape(str(data.get('card_holder', NO_HOLDER)))}</b>",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await state.set_state(WithdrawalFlow.END)


def _details_for(admin_id: int, payment_info: str) -> str:
    """Полные реквизиты нужны только тому, кто платит (денежные админы); остальным — маска."""
    return str(payment_info) if is_money_admin(admin_id) else payment_details.mask(payment_info)


# ======================================================
# 6. Подтверждение → БД + уведомление админам
# ======================================================
@router.callback_query(F.data == "confirm_withdraw", WithdrawalFlow.END)
async def confirm_withdraw(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    data = await state.get_data()
    user_id = call.from_user.id
    # Финансовый админ: без «раз в сутки» и без нормы добавленных участников
    money_admin = is_money_admin(user_id)

    # Быстрая проверка, чтобы не гонять расчёт баланса зря.
    # Окончательная — в add_payout_request, в одном SQL со вставкой (#47)
    if not money_admin:
        existing = await db.get_pending_or_today_payout(user_id)
        if existing:
            await call.message.answer(ALREADY_REQUESTED_TEXT)
            await state.clear()
            return

    balance = data.get("balance")
    payment_info = data.get("payment_info")
    card_holder = str(data.get("card_holder") or NO_HOLDER)

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
    settings = get_settings()
    required_members = 0 if money_admin else settings.group_member_count
    if required_members > 0:
        if settings.group_id is None:
            await call.message.answer(GROUP_NOT_SET_TEXT)
            await state.clear()
            return
        added_row = await db.get_user_add_count(settings.group_id, user_id)
        added = int(added_row["add_user_count"] or 0) if added_row else 0
        if added < required_members:
            await call.message.answer(
                f"⚠️ Сейчас добавлено <b>{added}</b> из <b>{required_members}</b> участников "
                "(кто-то вышел из группы). Добавьте недостающих и начните вывод заново.",
                parse_mode="HTML",
            )
            await state.clear()
            return

    # Имя держателя сохраняем в заявке: его видно в «💰 Заявки на выплаты».
    # Раньше оно было только в уведомлении админам (#51)
    holder_to_store = None if card_holder == NO_HOLDER else card_holder
    request_id = await db.add_payout_request(
        user_id=user_id,
        full_name=call.from_user.full_name,
        username=call.from_user.username or "—",
        card_number=payment_info,
        amount=balance,
        card_holder=holder_to_store,
        allow_repeat_today=money_admin,
    )
    if request_id is None:
        # Заявку успели создать параллельно (второй экземпляр бота, двойное нажатие) (#47)
        logger.warning(f"Повторная заявка на вывод отклонена базой: user={user_id}")
        await call.message.answer(ADMIN_PENDING_TEXT if money_admin else ALREADY_REQUESTED_TEXT)
        await state.clear()
        return

    # Запоминаем реквизиты только после успешной заявки: в следующий раз их не нужно вводить
    remembered = await _remember_details(user_id, data.get("withdraw_type"), payment_info, holder_to_store)

    # В журнал действий — только последние 4 цифры реквизитов
    await audit_log_service.record_user_action(
        actor_id=user_id,
        action="withdrawal_requested",
        entity_type="payout_request",
        entity_id=request_id,
        new_value={"amount": balance, "payment_info": _mask_payment_info(payment_info)},
        payload={"withdraw_type": data.get("withdraw_type"), "user_id": user_id, "money_admin": money_admin},
    )
    await activity_service.record(
        user_id=user_id,
        event="withdrawal_requested",
        chat_id=call.message.chat.id if call.message else None,
        session_id=call.id,
        payload={"amount": balance, "request_id": request_id},
    )

    remembered_note = "\n\n💾 Реквизиты сохранены — в следующий раз их не нужно вводить." if remembered else ""
    await call.message.edit_text(
        "✅ Заявка на вывод отправлена!\nОжидайте ответа администратора." + remembered_note,
        parse_mode="HTML",
    )
    await state.clear()

    # Имя и держатель экранируются: иначе Telegram отклонял сообщение и админ не узнавал о заявке
    admin_mark = "\n👑 Заявка финансового администратора" if money_admin else ""
    for admin in ADMINS:
        try:
            await call.bot.send_message(
                admin,
                f"📢 <b>Новая заявка на вывод</b> #{request_id}{admin_mark}\n\n"
                f"👤 <a href='tg://user?id={user_id}'>{html.escape(call.from_user.full_name)}</a>\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"💰 Сумма: <b>{balance}</b> сум\n"
                f"💳 Реквизиты: <code>{html.escape(_details_for(admin, payment_info))}</code>\n"
                f"👤 Держатель: <b>{html.escape(card_holder)}</b>",
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
