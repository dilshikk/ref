# /www/wwwroot/uz_ibot/handlers/groups/member_events.py
import asyncio
from typing import Union

from aiogram import Router, types
from aiogram.filters import BaseFilter
from aiogram.types import Message, ChatMemberUpdated
from loguru import logger

from data.config import url_yaml, ADMINS
from loader import db, bot

router = Router(name="groups.member_events")


class ChatGroupFilter(BaseFilter):
    def __init__(self, chat_types: Union[str, list[str]] | None = None):
        self.chat_types = chat_types or ["group", "supergroup"]

    async def __call__(self, event: Union[Message, ChatMemberUpdated]) -> bool:
        return event.chat.type in self.chat_types


async def _delete_after(chat_id: int, message_id: int, delay: int = 30) -> None:
    """Удаляет сообщение через delay секунд в фоне."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {message_id}: {e}")


async def _send_welcome_dm(user: types.User, group_id: int) -> None:
    """
    Пытается отправить личное сообщение новому участнику.
    Если Forbidden (не запускал бота) — отправляет в группу
    и удаляет через 30 сек в фоне.
    """
    bot_info = await bot.me()

    try:
        await bot.send_message(
            user.id,
            f"👋 Привет, <b>{user.full_name}</b>!\n\n"
            f"Ты вступил в группу.\n\n"
            f"🤑 Приглашай друзей и получай по "
            f"<b>{url_yaml.get('invited_credit', 0)}</b> сум за каждого!\n\n"
            f"👇 Нажми кнопку чтобы запустить бота:",
            parse_mode="HTML",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(
                    text="🚀 Запустить бота",
                    url=f"https://t.me/{bot_info.username}?start=from_group",
                )
            ]]),
        )
        logger.info(f"✉️ Личное приветствие отправлено user={user.id}")

    except Exception:
        # Пользователь не запускал бота — пишем в группу и удаляем через 30 сек
        try:
            msg = await bot.send_message(
                group_id,
                f"👋 {user.mention_html()}, добро пожаловать!\n"
                f"➡️ <a href='https://t.me/{bot_info.username}?start=from_group'>Запусти бота</a> "
                f"чтобы начать зарабатывать 🤑",
                parse_mode="HTML",
            )
            # Фоновая задача — хэндлер не блокируется
            asyncio.create_task(_delete_after(group_id, msg.message_id, delay=30))
            logger.info(f"📢 Приветствие в группу user={user.id}, удалится через 30 сек")

        except Exception as e:
            logger.warning(f"Не удалось отправить приветствие для user={user.id}: {e}")


async def credit_referral(
    referrer_id: int,
    new_user_id: int,
    new_user_name: str = "Пользователь",
) -> None:
    """Засчитывает реферал: проверяет дубли, антифрод, начисляет бонус."""
    already_invited = await db.select_invited_members(inveted_user_id=new_user_id)
    if already_invited:
        logger.debug(f"Реферал {new_user_id} уже засчитан, пропуск.")
        return

    count_last_hour = await db.get_referrals_last_hour(referrer_id)
    if count_last_hour >= 100:
        logger.warning(f"🚨 Антифрод: {referrer_id} пригласил {count_last_hour} за час!")
        for admin_id in ADMINS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚨 <b>Подозрительная активность!</b>\n\n"
                    f"👤 <a href='tg://user?id={referrer_id}'>{referrer_id}</a>\n"
                    f"👥 Приглашено за час: <b>{count_last_hour}</b>",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")
        return

    invited_by_user = await db.get_user_invited_count(referrer_id)
    if invited_by_user:
        await db.update_invite_current_history_count_plus_1(referrer_id)
    else:
        await db.add_user_invite_count(referrer_id, 1, 1)

    await db.add_user_invite_member(referrer_id, new_user_id)

    invited_credit = url_yaml.get("invited_credit", 0)
    try:
        await bot.send_message(
            referrer_id,
            f"🎉 <b>{new_user_name} вступил по вашей ссылке!</b>\n\n"
            f"💰 Вам начислено <b>{invited_credit}</b> сум.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")

    logger.info(f"✅ Реферал засчитан: new={new_user_id} → referrer={referrer_id}")


@router.chat_member(ChatGroupFilter())
async def on_user_join(event: types.ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return

    group_id = event.chat.id
    user = event.new_chat_member.user
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # Дебаг всех chat_member событий
    logger.debug(
        f"🔄 ChatMemberUpdate: {user.full_name} ({user.id}) | "
        f"{old_status} → {new_status} | "
        f"from={event.from_user} | "
        f"invite_link={event.invite_link.invite_link if event.invite_link else None}"
    )

    if user.is_bot:
        return

    # Только новые вступления
    if new_status != "member" or old_status not in ("left", "kicked"):
        return

    # Убеждаемся что пользователь есть в таблице Users
    existing_user = await db.select_user(telegram_id=user.id)
    if not existing_user:
        await db.add_user(
            full_name=user.full_name,
            username=user.username,
            telegram_id=user.id,
        )

    # Определяем реферера по инвайт-ссылке
    referrer_id: int | None = None

    if event.invite_link:
        link_str = event.invite_link.invite_link
        referrer_id = await db.get_user_by_invite_link(link_str)
        logger.debug(f"🔗 Ссылка: {link_str} → владелец: {referrer_id}")

    # Fallback: добавлен вручную
    if not referrer_id and event.from_user and event.from_user.id != user.id:
        inviter = event.from_user
        try:
            await db.add_or_update_group_member(group_id, inviter.id)
            logger.info(f"➕ {inviter.full_name} добавил {user.full_name} вручную")
        except Exception as e:
            logger.error(f"Ошибка add_or_update_group_member: {e}")
        await _send_welcome_dm(user, group_id)
        return

    if not referrer_id:
        # Вступил сам без реферальной ссылки
        try:
            existing = await db.get_user_add_count(group_id, user.id)
            if not existing:
                await db.execute(
                    "INSERT INTO GroupMembers (group_id, user_id, add_user_count) VALUES ($1, $2, 0)",
                    parameters=(group_id, user.id),
                )
        except Exception as e:
            logger.error(f"Ошибка при вставке участника без реферала: {e}")
        await _send_welcome_dm(user, group_id)
        return

    if referrer_id == user.id:
        logger.debug("Пользователь вступил по собственной ссылке, пропуск.")
        await _send_welcome_dm(user, group_id)
        return

    # Реферер найден — засчитываем
    try:
        await credit_referral(referrer_id, user.id, user.full_name)
        await db.add_or_update_group_member(group_id, referrer_id)
    except Exception as e:
        logger.error(f"Ошибка credit_referral [user={user.id}, ref={referrer_id}]: {e}")

    await _send_welcome_dm(user, group_id)


@router.message(ChatGroupFilter())
async def debug_all_messages(message: types.Message) -> None:
    logger.warning(f"📨 Сообщение в группе: {message.text or '[без текста]'}")


@router.my_chat_member(ChatGroupFilter())
async def debug_my_chat_member(event: types.ChatMemberUpdated) -> None:
    logger.warning(f"🤖 my_chat_member: {event.new_chat_member.status}")
