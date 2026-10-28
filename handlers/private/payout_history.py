# /www/wwwroot/uz_ibot/handlers/private/payout_history.py
from aiogram import Router, types, F
from filters import private_only
from loader import db

router = private_only(Router(name="private.payout_history"))

@router.message(F.text == "📜 История выплат")
async def payout_history(message: types.Message):
    rows = await db.get_user_payouts(message.from_user.id)
    if not rows:
        await message.answer("📭 У вас пока нет заявок на выплату.")
        return

    status_map = {"pending": "⏳ В обработке", "approved": "✅ Выплачено", "rejected": "❌ Отклонено"}
    text = "📜 <b>История ваших заявок:</b>\n\n"
    for row in rows:
        status = status_map.get(row["status"], row["status"])
        text += (
            f"🆔 #{row['id']} | {status}\n"
            f"💰 {row['amount']} сум | 💳 <code>{row['card_number']}</code>\n"
            f"📅 {str(row['created_at'])[:16]}\n\n"
        )
    await message.answer(text, parse_mode="HTML")
