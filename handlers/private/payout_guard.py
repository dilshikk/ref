# handlers/private/payout_guard.py
"""
Исправленные обработчики одобрения/отклонения выплат.
Роутер подключается ДО admin_panel, поэтому старые обработчики
approved_/rejected_/payout_receipt_input из admin_panel больше не срабатывают.

Исправлено:
- заявку нельзя одобрить/отклонить повторно (атомарная проверка status='pending');
- при одобрении списывается только выплаченная сумма, остаток баланса сохраняется;
- при одобрении из счётчика «добавил N участников» списывается норма для вывода
  (group_member_count), чтобы одних и тех же людей нельзя было использовать повторно;
- кнопки «одобрить/отклонить» убираются только после окончательного решения:
  если админ нажал «одобрить» и не прислал чек, заявку можно добить или отклонить позже;
- шаг с чеком можно отменить, админ больше не «застревает» в нём;
- выплата не одобряется, если баланс пользователя стал меньше суммы заявки
  (например, приглашённые вышли из группы), — раньше баланс молча уходил в ноль.
"""
from typing import Any, cast

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from loguru import logger

from data.config import ADMINS, GROUP_ID, url_yaml
from filters import private_only
from filters.admin import IsBotAdminFilter
from keyboards.reply_keyboards import finance_kb
from loader import db
from states.flows import AdminFlow
from handlers.private.admin_panel import _send_payout_report

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


def _required_members() -> int:
    try:
        return max(0, int(url_yaml.get("group_member_count", 3) or 0))
    except (TypeError, ValueError):
        return 0


def _invited_credit() -> int:
    try:
        return max(0, int(url_yaml.get("invited_credit", 0) or 0))
    except (TypeError, ValueError):
        return 0


async def _remove_buttons(call: types.CallbackQuery) -> None:
    message_any = cast(Any, call.message)
    if hasattr(message_any, "edit_reply_markup"):
        try:
            await message_any.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


async def _remove_buttons_by_id(message: types.Message, chat_id: int, message_id: int) -> None:
    """Убирает кнопки с исходного сообщения заявки, когда решение принято окончательно."""
    if message.bot is None or not chat_id or not message_id:
        return
    try:
        await message.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        pass


def _parse_req_id(data: str) -> int | None:
    parts = data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


@router.callback_query(F.data.startswith("approved_"), IsBotAdminFilter(ADMINS))
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


@router.callback_query(F.data.startswith("rejected_"), IsBotAdminFilter(ADMINS))
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


@router.message(AdminFlow.payout_receipt_input, IsBotAdminFilter(ADMINS))
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

    # Баланс мог уменьшиться после подачи заявки (приглашённые вышли из группы).
    # Раньше списание всё равно проходило и баланс молча обнулялся — деньги уходили «в минус».
    amount = int(payout_row.get("amount") or 0)
    user_id = int(payout_row["user_id"])
    record = await db.get_user_invited_count(user_id)
    current_balance = int(record["balance_sum"] or 0) if record else 0
    if current_balance < amount:
        await state.clear()
        await message.answer(
            f"⛔ Выплата #{req_id} не подтверждена.\n"
            f"Сумма заявки: {amount} сум, а баланс пользователя сейчас: {current_balance} сум "
            "(часть приглашённых, скорее всего, вышла из группы).\n"
            "Отклоните заявку — пользователь сможет подать новую на актуальную сумму.",
            reply_markup=finance_kb(),
        )
        return

    # Списываем только выплаченную сумму (в рефералах, с округлением вверх),
    # а не обнуляем весь баланс: остаток и новые рефералы сохраняются.
    invited_credit = _invited_credit()
    credits_to_deduct = -(-amount // invited_credit) if invited_credit > 0 else 0

    approved = cast(
        dict[str, Any] | None,
        await db.approve_payout_if_pending(
            req_id,
            credits_to_deduct,
            group_id=GROUP_ID,
            members_to_deduct=_required_members(),
        ),
    )
    await _remove_buttons_by_id(message, msg_chat_id, msg_id)
    if not approved:
        await message.answer("⚠️ Заявка уже обработана (одобрена или отклонена).", reply_markup=finance_kb())
        await state.clear()
        return

    if message.bot is not None:
        try:
            await message.bot.send_message(int(approved["user_id"]), "✅ Средства успешно переведены на ваш счёт!")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя о выплате: {e}")

    report_sent = False
    try:
        report_sent = await _send_payout_report(message, payout_row, admin_id=admin_id, include_receipt=include_receipt)
    except Exception as e:
        logger.warning(f"Не удалось отправить отчет о выплате в группу: {e}")

    if report_sent:
        await message.answer("✅ Выплата подтверждена. Отчет отправлен в группу выплат.", reply_markup=finance_kb())
    else:
        await message.answer(
            "✅ Выплата подтверждена.\n"
            "⚠️ Отчет в группу не отправлен: задайте payment_chat_id (или @channel / -100...) в конфиге.",
            reply_markup=finance_kb(),
        )
    await state.clear()
