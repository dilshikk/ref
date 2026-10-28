# /www/wwwroot/uz_ibot/handlers/private/wallet.py
from aiogram import Router, types, F
from loguru import logger

from filters import private_only
from data.config import url_yaml
from loader import db

router = private_only(Router(name="private.wallet"))


@router.message(F.text == "💰 Баланс")
async def balance(message: types.Message) -> None:
    user_id = message.from_user.id

    try:
        record = await db.get_user_invited_count(user_id)
        pending_payouts = await db.get_pending_payouts_by_user(user_id)
    except Exception as e:
        logger.error(f"balance db error [user={user_id}]: {e}")
        await message.answer("⚠️ Не удалось загрузить баланс. Попробуйте позже.")
        return

    current_count: int = record["current_count"] if record else 0
    history_count: int = record["history_count"] if record else 0
    invited_credit: int = url_yaml.get("invited_credit", 0)
    min_balance: int = url_yaml.get("min_balance", 0)

    total_earned: int = history_count * invited_credit

    # ✅ Защита от None в поле amount
    pending: int = int(sum(
        float(row["amount"] or 0) for row in pending_payouts
    )) if pending_payouts else 0

    # Сумма заявки списывается с баланса только при одобрении, поэтому
    # вычитаем ожидающие выплаты, чтобы не показывать одни деньги дважды.
    available: int = max(0, current_count * invited_credit - pending)

    # Кнопку вывода не показываем, пока есть заявка в ожидании:
    # повторная заявка всё равно будет отклонена.
    markup = (
        types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="💸 Вывести средства", callback_data="go_withdraw")
        ]])
        if available > 0 and available >= min_balance and pending == 0
        else None
    )

    await message.answer(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"✅ Доступно к выводу: <b>{available}</b> сум\n"
        f"📊 Всего заработано: <b>{total_earned}</b> сум\n"
        f"⏳ Ожидает обработки: <b>{pending}</b> сум\n\n"
        f"📈 Минимум для вывода: <b>{min_balance}</b> сум",
        parse_mode="HTML",
        reply_markup=markup,
    )
