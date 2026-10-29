# /www/wwwroot/uz_ibot/handlers/private/referrals.py
from aiogram import types, Router, F
from loguru import logger

from filters import private_only
from data.settings import get_settings, is_http_url
from loader import db, bot                        # ✅ исправлен импорт
from services.invite import ensure_invite_link    # ✅ отдельная строка

router = private_only(Router(name="private.referrals"))


# ─────────────────────────────────────────────
# 🔗 Реферальная ссылка (персональная инвайт-ссылка в группу)
# ─────────────────────────────────────────────
@router.message(F.text == "🔗 Моя реферальная ссылка")
async def referral_link(message: types.Message) -> None:
    user_id = message.from_user.id

    user = await db.select_user(telegram_id=user_id)  # ✅ именованный аргумент
    if not user:
        await message.answer("⚠️ Сначала вступите в группу.")
        return

    try:
        link = await ensure_invite_link(bot, user_id)
    except Exception as e:
        logger.error(f"referral_link — не удалось получить инвайт [user={user_id}]: {e}")
        await message.answer("⚠️ Не удалось сформировать ссылку. Попробуйте позже.")
        return

    await message.answer(
        "ℹ️ Приглашайте друзей по вашей реферальной ссылке и получайте вознаграждения!\n\n"
        "Ваша персональная ссылка 👇\n\n"
        f"<code>{link}</code>",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
# 📢 Чат выплат
# ─────────────────────────────────────────────
@router.message(F.text.in_({"📢 Чат выплат", "📜 Канал выплат"}))
async def payment_channel(message: types.Message) -> None:
    channel_link = get_settings().payment_channel
    if is_http_url(channel_link):
        await message.answer(
            "📢 <b>Чат/канал выплат</b>\n\n"
            "Здесь публикуются подтверждения выплат, чтобы вы могли видеть прозрачность транзакций:\n"
            f"{channel_link}",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "ℹ️ Публичная ссылка чата выплат пока не настроена.\n"
        "Обратитесь в поддержку: мы подскажем актуальный канал подтверждений.",
    )


# ─────────────────────────────────────────────
# 🏆 Топ-10 рефералов
# ─────────────────────────────────────────────
_MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_TOP_LIMIT = len(_MEDALS)


@router.message(F.text == "/top10")
async def top_referrals(message: types.Message) -> None:
    try:
        rows = await db.get_top_referrals(_TOP_LIMIT)
    except Exception as e:
        logger.error(f"top_referrals db error: {e}")
        await message.answer("⚠️ Не удалось загрузить топ. Попробуйте позже.")
        return

    if not rows:
        await message.answer("😔 Пока нет данных.")
        return

    lines: list[str] = []

    for i, row in enumerate(rows[:_TOP_LIMIT]):
        name: str = row["full_name"] or "—"
        username_part = f"@{row['username']}" if row.get("username") else ""
        count: int = row["history_count"]
        lines.append(
            f"{_MEDALS[i]} {name} {username_part}\n"
            f"   👥 {count} чел. | 💰 {int(row.get('earned_sum') or 0)} сум"
        )

    await message.answer(
        "🏆 <b>Топ рефералов</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
# 👥 Мои приглашённые
# ─────────────────────────────────────────────
@router.message(F.text == "👥 Мои приглашённые")
async def my_invited(message: types.Message) -> None:
    user_id = message.from_user.id

    try:
        members = await db.select_invited_members(user_id=user_id)
        record = await db.get_user_invited_count(user_id)
    except Exception as e:
        logger.error(f"my_invited db error [user={user_id}]: {e}")
        await message.answer("⚠️ Не удалось загрузить данные. Попробуйте позже.")
        return

    if not members:
        await message.answer(
            "😔 Вы ещё никого не пригласили.\n\n"
            "📎 Поделитесь своей реферальной ссылкой!"
        )
        return

    current_count: int = record["current_count"] if record else 0
    history_count: int = record["history_count"] if record else 0
    earned_sum: int = int(record["earned_sum"] or 0) if record else 0

    await message.answer(
        f"👥 <b>Ваши приглашённые</b>\n\n"
        f"✅ Активных: <b>{current_count}</b>\n"
        f"📊 Всего приглашено: <b>{history_count}</b>\n"
        f"💰 Заработано всего: <b>{earned_sum}</b> сум",
        parse_mode="HTML",
    )
