# handlers/private/referral_holds.py
"""
Проверка рефералов «на удержании».

Вступления сверх антифрод-лимита больше не теряются: они записываются в
referral_holds. Здесь админ видит, у кого сколько отложено, и решает:
начислить всё по текущей цене или отклонить (накрутка).
"""
from typing import Any, cast

from aiogram import F, Router, types
from aiogram.filters import Command
from loguru import logger

from data.config import ADMINS, GROUP_ID
from data.settings import get_settings
from filters import private_only
from filters.admin import IsBotAdminFilter, IsMoneyAdmin
from loader import db
from services.audit_log import audit_log_service

router = private_only(Router(name="private.referral_holds"))


def _decision_kb(referrer_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Начислить", callback_data=f"hold_ok_{referrer_id}"),
        types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"hold_no_{referrer_id}"),
    ]])


def _parse_referrer(data: str) -> int | None:
    tail = data.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


@router.message(Command("holds"), IsBotAdminFilter(ADMINS))
async def list_holds(message: types.Message) -> None:
    rows = cast(list[dict[str, Any]], await db.list_pending_referral_holds() or [])
    if not rows:
        await message.answer("✅ Рефералов на удержании нет.")
        return
    price = get_settings().invited_credit
    await message.answer(
        f"⏸ <b>Рефералы на удержании</b> (превышен лимит в час)\n"
        f"Начисление идёт по текущей цене: <b>{price}</b> сум за человека.",
        parse_mode="HTML",
    )
    for row in rows:
        referrer_id = int(row["referrer_id"])
        held = int(row["held"] or 0)
        await message.answer(
            f"👤 <a href='tg://user?id={referrer_id}'>{referrer_id}</a>\n"
            f"⏸ Отложено вступлений: <b>{held}</b> (≈ {held * price} сум)\n"
            f"🕒 Первое: {row['first_at']}",
            parse_mode="HTML",
            reply_markup=_decision_kb(referrer_id),
        )


async def _notify(call: types.CallbackQuery, user_id: int, text: str) -> None:
    if call.bot is None:
        return
    try:
        await call.bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Не удалось уведомить {user_id} о решении по удержанию: {e}")


async def _drop_buttons(call: types.CallbackQuery, note: str) -> None:
    msg = cast(Any, call.message)
    if msg is None:
        return
    try:
        await msg.edit_text(f"{msg.html_text}\n\n{note}", parse_mode="HTML", reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("hold_ok_"), IsBotAdminFilter(ADMINS), IsMoneyAdmin())
async def approve_holds(call: types.CallbackQuery) -> None:
    referrer_id = _parse_referrer(call.data or "")
    if referrer_id is None:
        await call.answer("⚠️ Некорректные данные кнопки.", show_alert=True)
        return
    price = get_settings().invited_credit
    result = await db.approve_referral_holds(referrer_id, call.from_user.id, price, GROUP_ID)
    if result["claimed"] == 0:
        await call.answer("⚠️ Уже обработано.", show_alert=True)
        await _drop_buttons(call, "ℹ️ Уже обработано другим админом.")
        return

    credited = result["credited"]
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="referral_holds_approved",
        entity_type="user",
        entity_id=str(referrer_id),
        new_value={"credited": credited, "price": price},
        payload={"claimed": result["claimed"]},
    )
    await call.answer("✅ Начислено.")
    await _drop_buttons(call, f"✅ Начислено {credited} × {price} = <b>{credited * price}</b> сум")
    if credited > 0:
        await _notify(
            call,
            referrer_id,
            f"✅ Администратор проверил отложенные приглашения.\n"
            f"💰 Начислено <b>{credited * price}</b> сум за {credited} чел.",
        )


@router.callback_query(F.data.startswith("hold_no_"), IsBotAdminFilter(ADMINS), IsMoneyAdmin())
async def reject_holds(call: types.CallbackQuery) -> None:
    referrer_id = _parse_referrer(call.data or "")
    if referrer_id is None:
        await call.answer("⚠️ Некорректные данные кнопки.", show_alert=True)
        return
    rejected = await db.reject_referral_holds(referrer_id, call.from_user.id)
    if rejected == 0:
        await call.answer("⚠️ Уже обработано.", show_alert=True)
        await _drop_buttons(call, "ℹ️ Уже обработано другим админом.")
        return
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="referral_holds_rejected",
        entity_type="user",
        entity_id=str(referrer_id),
        payload={"rejected": rejected},
    )
    await call.answer("❌ Отклонено.")
    await _drop_buttons(call, f"❌ Отклонено вступлений: <b>{rejected}</b>")
    await _notify(
        call,
        referrer_id,
        f"❌ Администратор проверил отложенные приглашения ({rejected}) и не подтвердил их.",
    )
