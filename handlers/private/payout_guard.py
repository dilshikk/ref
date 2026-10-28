# handlers/private/payout_guard.py
"""
Исправленные обработчики одобрения/отклонения выплат.
Роутер подключается ДО admin_panel, поэтому старые обработчики
approved_/rejected_/payout_receipt_input из admin_panel больше не срабатывают.

Исправлено:
- заявку нельзя одобрить/отклонить повторно (атомарная проверка status='pending');
- при одобрении списывается только выплаченная сумма, остаток баланса сохраняется;
- при одобрении из счётчика «добавил N участников» списывается норма для вывода
  (group_member_count), чтобы одних и тех же людей нельзя было использовать повторно.
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
from handlers.private.admin_panel import _payout_receipt_kb, _send_payout_report

router = private_only(Router(name="private.payout_guard"))


def _required_members() -> int:
    try:
        return max(0, int(url_yaml.get("group_member_count", 3) or 0))
    except (TypeError, ValueError):
        return 0


async def _remove_buttons(call: types.CallbackQuery) -> None:
    message_any = cast(Any, call.message)
    if hasattr(message_any, "edit_reply_markup"):
        try:
            await message_any.edit_reply_markup(reply_markup=None)
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
    await _remove_buttons(call)
    if not payout_row or payout_row.get("status") != "pending":
        await call.answer("⚠️ Заявка уже обработана.", show_alert=True)
        return

    await state.update_data(
        payout_user_id=int(payout_row["user_id"]),
        payout_req_id=req_id,
        payout_admin_id=call.from_user.id,
    )
    await state.set_state(AdminFlow.payout_receipt_input)
    await cast(Any, call.message).answer(
        "Пришлите чек перевода (фото/документ) или нажмите «⏭ Без чека».\n"
        "После этого выплата будет подтверждена окончательно.",
        reply_markup=_payout_receipt_kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rejected_"), IsBotAdminFilter(ADMINS))
async def payment_canceled(call: types.CallbackQuery) -> None:
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
    if req_id <= 0 or admin_id <= 0:
        await message.answer("⚠️ Сессия подтверждения устарела. Повторите через список заявок.", reply_markup=finance_kb())
        await state.clear()
        return

    include_receipt = False
    if message.text:
        if message.text.strip() != "⏭ Без чека":
            await message.answer("Отправьте фото/документ чека или нажмите «⏭ Без чека».", reply_markup=_payout_receipt_kb)
            return
    elif message.photo or message.document:
        include_receipt = True
    else:
        await message.answer("Поддерживается чек как фото/документ или кнопка «⏭ Без чека».", reply_markup=_payout_receipt_kb)
        return

    payout_row = cast(dict[str, Any] | None, await db.get_payout_request_by_id(req_id))
    if not payout_row:
        await message.answer("⚠️ Заявка не найдена.", reply_markup=finance_kb())
        await state.clear()
        return

    # Списываем только выплаченную сумму (в рефералах, с округлением вверх),
    # а не обнуляем весь баланс: остаток и новые рефералы сохраняются.
    invited_credit = int(url_yaml.get("invited_credit", 1) or 1)
    amount = int(payout_row.get("amount") or 0)
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
