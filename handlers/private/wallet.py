# handlers/private/wallet.py
from aiogram import Router, types, F
from loguru import logger

from filters import private_only
from data.settings import get_settings
from loader import db
from services.group_adds import locked_explanation, unverified_referral_sum
from utils.payout_math import pending_total, withdrawable

router = private_only(Router(name="private.wallet"))


@router.message(F.text == "💰 Баланс")
async def balance(message: types.Message) -> None:
    user_id = message.from_user.id

    try:
        record = await db.get_user_invited_count(user_id)
        pending_payouts = await db.get_pending_payouts_by_user(user_id)
        locked = await unverified_referral_sum(user_id)
    except Exception as e:
        logger.error(f"balance db error [user={user_id}]: {e}")
        await message.answer("⚠️ Не удалось загрузить баланс. Попробуйте позже.")
        return

    # Деньги берём из базы как есть: смена цены за реферала их не пересчитывает
    balance_sum: int = int(record["balance_sum"] or 0) if record else 0
    total_earned: int = int(record["earned_sum"] or 0) if record else 0
    min_balance = get_settings().min_balance

    # Расчёт вынесен в utils/payout_math.py и покрыт тестами (#24, #46)
    pending = pending_total(row["amount"] for row in pending_payouts or [])
    available = withdrawable(balance_sum, locked, pending)

    # Кнопку вывода не показываем, пока есть заявка в ожидании:
    # повторная заявка всё равно будет отклонена.
    markup = (
        types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="💸 Вывести средства", callback_data="go_withdraw")
        ]])
        if available > 0 and available >= min_balance and pending == 0
        else None
    )

    locked_line = ""
    if locked > 0:
        explanation = locked_explanation()
        locked_line = f"🔒 На проверке: <b>{locked}</b> сум\n" + (f"<i>{explanation}</i>\n" if explanation else "")

    # Долг появляется, если приглашённые вышли из группы уже после выплаты (#46)
    debt_line = (
        f"⚠️ Долг: <b>{-balance_sum}</b> сум — приглашённые вышли из группы после выплаты. "
        "Он погасится следующими начислениями.\n"
        if balance_sum < 0
        else ""
    )

    await message.answer(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"✅ Доступно к выводу: <b>{available}</b> сум\n"
        f"{locked_line}"
        f"{debt_line}"
        f"📊 Всего заработано: <b>{total_earned}</b> сум\n"
        f"⏳ Ожидает обработки: <b>{pending}</b> сум\n\n"
        f"📈 Минимум для вывода: <b>{min_balance}</b> сум",
        parse_mode="HTML",
        reply_markup=markup,
    )
