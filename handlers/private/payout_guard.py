# handlers/private/payout_guard.py
"""
Обработчики одобрения/отклонения выплат — единственные в проекте.
Старые дубли approved_/rejected_/payout_receipt_input из admin_panel удалены (#35).

Исправлено:
- заявку нельзя одобрить/отклонить повторно (атомарная проверка status='pending');
- при одобрении списывается только выплаченная сумма, остаток баланса сохраняется;
- при одобрении из счётчика «добавил N участников» списывается норма для вывода
  (group_member_count), чтобы одних и тех же людей нельзя было использовать повторно;
- кнопки «одобрить/отклонить» убираются только после окончательного решения:
  если админ нажал «одобрить» и не прислал чек, заявку можно добить или отклонить позже;
- шаг с чеком можно отменить, админ больше не «застревает» в нём;
- выплата не одобряется, если баланс пользователя за вычетом денег «на проверке»
  (#46) стал меньше суммы заявки или число добавленных участников стало меньше
  нормы. Проверка идёт в том же SQL, что и списание, — атомарно.
- одобрять/отклонять выплаты может только денежный админ (#23); остальным админам
  отвечает money_guard. Список денежных админов и ID группы берутся из
  bot_settings (#116) при каждом действии — изменения из админки действуют сразу.
- отчёт о выплате отправляется через services/payouts.py, а не через
  внутренние функции admin_panel (#36).
- список «💰 Заявки на выплаты» перехватывается здесь: полный номер карты видит
  только денежный админ, остальные админы — маску ****1234 (#20).
"""
import html
from typing import Any, cast

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from loguru import logger

from data.config import ADMINS
from data.settings import get_settings
from filters import private_only
from filters.admin import IsAdminWithoutMoneyRights, IsBotAdminFilter, IsMoneyAdmin, is_money_admin
from keyboards.reply_keyboards import finance_kb
from loader import db
from services.group_adds import qualification_enabled
from services.payouts import send_payout_report
from services.settings_store import GROUP_NOT_SET_TEXT
from states.flows import AdminFlow
from utils import payment_details
from utils.payout_math import approval_rejection_reasons, credits_for_amount

router = private_only(Router(name="private.payout_guard"))

_NO_RECEIPT = "⏭ Без чека"
_CANCEL_CONFIRM = "❌ Отменить подтверждение"

_receipt_kb = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text=_NO_RECEIPT)],
        [types.KeyboardButton(text=_CANCEL_CONFIRM)],
    ],
    resize_keyboard=True,
)


async def _remove_buttons(call: types.CallbackQuery) -> None:
    message_any = cast(Any, call.message)
    if hasattr(message_any, "edit_reply_markup"):
        try:
            await message_any.edit_reply_markup(reply_markup=None)
        except TelegramAPIError as e:
            logger.debug(f"Не удалось убрать кнопки заявки: {e}")


async def _remove_buttons_by_id(message: types.Message, chat_id: int, message_id: int) -> None:
    """Убирает кнопки с исходного сообщения заявки, когда решение принято окончательно."""
    if message.bot is None or not chat_id or not message_id:
        return
    try:
        await message.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramAPIError as e:
        logger.debug(f"Не удалось убрать кнопки заявки (сообщение {message_id}): {e}")


def _parse_req_id(data: str) -> int | None:
    parts = data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


# Денежные кнопки, которые может нажимать только денежный админ (#23).
_MONEY_CALLBACK_PREFIXES = ("approved_", "rejected_", "umgmt_addbal_", "umgmt_reset_", "umgmt_resetok_", "hold_ok_", "hold_no_")


@router.message(F.text == "💰 Заявки на выплаты", IsBotAdminFilter(ADMINS))
async def list_payouts(message: types.Message) -> None:
    """Список заявок: реквизиты полностью — только денежному админу, остальным маска (#20)."""
    payouts = cast(list[dict[str, Any]], await db.get_pending_payouts() or [])
    if not payouts:
        await message.answer("✅ Нет активных заявок на выплату.")
        return

    await message.answer(f"💸 <b>Заявок на выплату: {len(payouts)}</b>", parse_mode="HTML")

    viewer_id = message.from_user.id if message.from_user else 0
    show_full = is_money_admin(viewer_id)

    for req in payouts:
        card = str(req.get("card_number") or "")
        card_text = card if show_full else payment_details.mask(card)
        markup = types.InlineKeyboardMarkup(
            inline_keyboard=[[
                types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approved_{req['user_id']}_{req['id']}"),
                types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rejected_{req['user_id']}_{req['id']}"),
            ]]
        )
        await message.answer(
            f"🆔 <b>{req['id']}</b> | "
            f"👤 <a href='tg://user?id={req['user_id']}'>{html.escape(str(req.get('full_name') or '-'))}</a>\n"
            f"💳 <code>{html.escape(card_text)}</code>\n"
            f"📅 {req.get('created_at')}",
            parse_mode="HTML",
            reply_markup=markup,
        )


@router.callback_query(F.data.startswith(_MONEY_CALLBACK_PREFIXES), IsAdminWithoutMoneyRights())
async def money_guard(call: types.CallbackQuery) -> None:
    """
    Обычный админ нажал денежную кнопку — сразу отвечаем понятным отказом,
    а не молчанием (сами денежные обработчики требуют IsMoneyAdmin).
    """
    logger.warning(f"Админ {call.from_user.id} без прав денежного админа нажал «{call.data}»")
    await call.answer("⛔ Выплаты и балансы может менять только финансовый администратор.", show_alert=True)


@router.callback_query(F.data.startswith("approved_"), IsBotAdminFilter(ADMINS), IsMoneyAdmin())
async def payment_confirmed(call: types.CallbackQuery, state: FSMContext) -> None:
    if not call.data or not call.message:
        return
    req_id = _parse_req_id(call.data)
    if req_id is None:
        await call.answer("⚠️ Некорректные данные кнопки.", show_alert=True)
        return

    payout_row = cast(dict[str, Any] | None, await db.get_payout_request_by_id(req_id))
    if not payout_row or payout_row.get("status") != "pending":
        await _remove_buttons(call)
        await call.answer("⚠️ Заявка уже обработана.", show_alert=True)
        return

    # Кнопки НЕ убираем: пока админ не прислал чек, заявка ещё не одобрена
    await state.update_data(
        payout_user_id=int(payout_row["user_id"]),
        payout_req_id=req_id,
        payout_admin_id=call.from_user.id,
        payout_msg_chat_id=call.message.chat.id,
        payout_msg_id=call.message.message_id,
    )
    await state.set_state(AdminFlow.payout_receipt_input)
    await cast(Any, call.message).answer(
        f"Заявка #{req_id}: пришлите чек перевода (фото/документ) или нажмите «{_NO_RECEIPT}».\n"
        f"После этого выплата будет подтверждена окончательно.\n"
        f"Передумали — нажмите «{_CANCEL_CONFIRM}», заявка останется в ожидании.",
        reply_markup=_receipt_kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rejected_"), IsBotAdminFilter(ADMINS), IsMoneyAdmin())
async def payment_canceled(call: types.CallbackQuery, state: FSMContext) -> None:
    if not call.data or not call.message:
        return
    req_id = _parse_req_id(call.data)
    if req_id is None:
        await call.answer("⚠️ Некорректные данные кнопки.", show_alert=True)
        return

    rejected = cast(dict[str, Any] | None, await db.reject_payout_if_pending(req_id))
    await _remove_buttons(call)
    if not rejected:
        await call.answer("⚠️ Заявка уже обработана.", show_alert=True)
        return
    await call.answer("❌ Заявка отклонена.")

    # Если админ начинал одобрять эту же заявку — выходим из шага с чеком
    data = await state.get_data()
    if int(data.get("payout_req_id") or 0) == req_id:
        await state.clear()
        await cast(Any, call.message).answer("Заявка отклонена.", reply_markup=finance_kb())

    if call.bot is None:
        return
    try:
        await call.bot.send_message(int(rejected["user_id"]), "❌ Выплата отменена. Попробуйте позже снова.")
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя об отклонении: {e}")


@router.message(AdminFlow.payout_receipt_input, IsBotAdminFilter(ADMINS), IsMoneyAdmin())
async def payout_finalize_with_receipt(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    req_id = int(data.get("payout_req_id") or 0)
    admin_id = int(data.get("payout_admin_id") or 0)
    msg_chat_id = int(data.get("payout_msg_chat_id") or 0)
    msg_id = int(data.get("payout_msg_id") or 0)
    if req_id <= 0 or admin_id <= 0:
        await message.answer("⚠️ Сессия подтверждения устарела. Повторите через список заявок.", reply_markup=finance_kb())
        await state.clear()
        return

    include_receipt = False
    if message.text:
        text = message.text.strip()
        # Отмена шага или любая команда — выходим, заявка остаётся в ожидании
        if text == _CANCEL_CONFIRM or text.startswith("/"):
            await state.clear()
            await message.answer(
                f"Подтверждение заявки #{req_id} отменено. Она осталась в ожидании — "
                "её можно одобрить или отклонить кнопками под заявкой.",
                reply_markup=finance_kb(),
            )
            return
        if text != _NO_RECEIPT:
            await message.answer(
                f"Отправьте фото/документ чека, нажмите «{_NO_RECEIPT}» или «{_CANCEL_CONFIRM}».",
                reply_markup=_receipt_kb,
            )
            return
    elif message.photo or message.document:
        include_receipt = True
    else:
        await message.answer(
            f"Поддерживается чек как фото/документ, «{_NO_RECEIPT}» или «{_CANCEL_CONFIRM}».",
            reply_markup=_receipt_kb,
        )
        return

    payout_row = cast(dict[str, Any] | None, await db.get_payout_request_by_id(req_id))
    if not payout_row:
        await message.answer("⚠️ Заявка не найдена.", reply_markup=finance_kb())
        await state.clear()
        return
    if payout_row.get("status") != "pending":
        await _remove_buttons_by_id(message, msg_chat_id, msg_id)
        await message.answer("⚠️ Заявка уже обработана (одобрена или отклонена).", reply_markup=finance_kb())
        await state.clear()
        return

    settings = get_settings()
    group_id = settings.group_id
    # Без группы нельзя проверить норму «добавил N участников» — не одобряем вслепую.
    # Заявка остаётся в ожидании, админ одобрит её после того, как укажет группу.
    if group_id is None:
        await state.clear()
        await message.answer(GROUP_NOT_SET_TEXT, reply_markup=finance_kb())
        return

    # Списываем только выплаченную сумму (в рефералах, с округлением вверх),
    # а не обнуляем весь баланс: остаток и новые рефералы сохраняются.
    amount = int(payout_row.get("amount") or 0)
    credits_to_deduct = credits_for_amount(amount, settings.invited_credit)
    required_members = settings.group_member_count
    hold_hours = settings.referral_hold_hours
    check_quality = qualification_enabled()

    # Баланс, деньги «на проверке» (#46) и норма «добавил N участников» проверяются
    # внутри того же SQL, что и списание: между проверкой и списанием никто не
    # успеет выйти из группы.
    approved = cast(
        dict[str, Any] | None,
        await db.approve_payout_if_pending(
            req_id,
            credits_to_deduct,
            group_id=group_id,
            members_to_deduct=required_members,
            hold_hours=hold_hours,
            check_quality=check_quality,
        ),
    )
    if not approved:
        await state.clear()
        info = cast(
            dict[str, Any] | None,
            await db.get_payout_approval_state(
                req_id, group_id, hold_hours=hold_hours, check_quality=check_quality
            ),
        )
        if not info or info.get("status") != "pending":
            await _remove_buttons_by_id(message, msg_chat_id, msg_id)
            await message.answer("⚠️ Заявка уже обработана (одобрена или отклонена).", reply_markup=finance_kb())
            return
        reasons = approval_rejection_reasons(
            balance=int(info.get("balance_sum") or 0),
            locked=int(info.get("locked_sum") or 0),
            added=int(info.get("add_user_count") or 0),
            amount=amount,
            required_members=required_members,
        )
        # Кнопки под заявкой оставляем: админ может отклонить её
        await message.answer(
            f"⛔ Выплата #{req_id} не подтверждена:\n" + "\n".join(reasons) + "\n\n"
            "Отклоните заявку — пользователь сможет подать новую, когда условия снова выполнятся.",
            reply_markup=finance_kb(),
        )
        return

    await _remove_buttons_by_id(message, msg_chat_id, msg_id)

    if message.bot is not None:
        try:
            await message.bot.send_message(int(approved["user_id"]), "✅ Средства успешно переведены на ваш счёт!")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя о выплате: {e}")

    report_sent = False
    try:
        report_sent = await send_payout_report(message, payout_row, admin_id=admin_id, include_receipt=include_receipt)
    except Exception as e:
        logger.warning(f"Не удалось отправить отчет о выплате в группу: {e}")

    if report_sent:
        await message.answer("✅ Выплата подтверждена. Отчет отправлен в группу выплат.", reply_markup=finance_kb())
    else:
        await message.answer(
            "✅ Выплата подтверждена.\n"
            "⚠️ Отчет в группу не отправлен: задайте чат отчётов в «💰 Финансы» → «🆔 Чат выплат».",
            reply_markup=finance_kb(),
        )
    await state.clear()
