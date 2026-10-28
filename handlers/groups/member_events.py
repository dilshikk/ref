# /www/wwwroot/uz_ibot/handlers/groups/member_events.py
import asyncio
from typing import Union

from aiogram import Router, types
from aiogram.filters import BaseFilter
from aiogram.types import Message, ChatMemberUpdated
from loguru import logger

from data.config import url_yaml, ADMINS, GROUP_ID
from loader import db, bot

router = Router(name="groups.member_events")

_DEFAULT_ANTIFRAUD_LIMIT = 100
# Сколько часов после вступления выход из группы отменяет начисление (пункт 8)
_DEFAULT_REFERRAL_HOLD_HOURS = 72

_LEFT_STATUSES = ("left", "kicked")


def _antifraud_limit() -> int:
    """Лимит рефералов в час из настроек админки (🚨 Антифрод лимит)."""
    try:
        value = int(url_yaml.get("antispam_referral_limit", _DEFAULT_ANTIFRAUD_LIMIT))
    except (TypeError, ValueError):
        return _DEFAULT_ANTIFRAUD_LIMIT
    return value if value > 0 else _DEFAULT_ANTIFRAUD_LIMIT


def _referral_hold_hours() -> int:
    """
    referral_hold_hours в url.yml: если приглашённый вышел из группы в течение
    этого времени после вступления, начисление снимается. 0 — снимать всегда.
    """
    try:
        value = int(url_yaml.get("referral_hold_hours", _DEFAULT_REFERRAL_HOLD_HOURS))
    except (TypeError, ValueError):
        return _DEFAULT_REFERRAL_HOLD_HOURS
    return max(0, value)


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
) -> bool:
    """Засчитывает реферал: проверяет дубли, антифрод, начисляет бонус.
    Возвращает True, только если реферал действительно засчитан сейчас."""
    already_invited = await db.select_invited_members(inveted_user_id=new_user_id)
    if already_invited:
        # Сюда же попадает повторное вступление после выхода: деньги второй раз не начисляются
        logger.debug(f"Реферал {new_user_id} уже засчитан, пропуск.")
        return False

    if await db.is_user_blocked(referrer_id):
        logger.info(f"⛔ Реферер {referrer_id} заблокирован — реферал не засчитан.")
        return False

    limit = _antifraud_limit()
    count_last_hour = await db.get_referrals_last_hour(referrer_id)
    if count_last_hour >= limit:
        logger.warning(
            f"🚨 Антифрод: {referrer_id} пригласил {count_last_hour} за час (лимит {limit})!"
        )
        for admin_id in ADMINS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚨 <b>Подозрительная активность!</b>\n\n"
                    f"👤 <a href='tg://user?id={referrer_id}'>{referrer_id}</a>\n"
                    f"👥 Приглашено за час: <b>{count_last_hour}</b> (лимит {limit})",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e}")
        return False

    # Цена фиксируется в момент начисления: если админ потом её поменяет,
    # уже заработанные деньги не пересчитаются
    invited_credit = int(url_yaml.get("invited_credit", 0) or 0)
    credited = await db.credit_referral_once(referrer_id, new_user_id, reward=invited_credit)
    if not credited:
        # Повторное событие от Telegram или параллельная обработка — второй раз не начисляем
        logger.debug(f"Реферал {new_user_id} уже засчитан (повторное событие), пропуск.")
        return False

    hold_hours = _referral_hold_hours()
    hold_note = (
        f"\n\n⚠️ Если друг выйдет из группы в течение {hold_hours} ч., начисление будет снято."
        if hold_hours > 0
        else "\n\n⚠️ Если друг выйдет из группы, начисление будет снято."
    )
    try:
        await bot.send_message(
            referrer_id,
            f"🎉 <b>{new_user_name} вступил по вашей ссылке!</b>\n\n"
            f"💰 Вам начислено <b>{invited_credit}</b> сум.{hold_note}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")

    logger.info(f"✅ Реферал засчитан: new={new_user_id} → referrer={referrer_id}")
    return True


async def reverse_referral_on_leave(user: types.User, group_id: int) -> None:
    """Пункт 8: приглашённый вышел из группы — снимаем начисление у пригласившего."""
    # Реферальные ссылки ведут только в GROUP_ID: выход из других групп не учитываем
    if group_id != GROUP_ID:
        return

    hold_hours = _referral_hold_hours()
    try:
        reversed_row = await db.reverse_referral_on_leave(user.id, hold_hours, group_id)
    except Exception as e:
        logger.error(f"Ошибка снятия реферала при выходе [user={user.id}]: {e}")
        return

    if not reversed_row:
        # Не был приглашён, уже снято или вышел позже срока удержания
        return

    referrer_id = int(reversed_row["user_id"])
    reward = int(reversed_row["reward_sum"] or 0)
    logger.info(f"↩️ Реферал снят: {user.id} вышел из группы → referrer={referrer_id}, -{reward} сум")

    try:
        await bot.send_message(
            referrer_id,
            f"↩️ <b>{user.full_name}</b> вышел из группы.\n\n"
            f"💸 Начисление <b>{reward}</b> сум за этого участника снято.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить реферера {referrer_id} о снятии: {e}")


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

    # Выход из группы (сам или удалён админом) — снимаем начисление
    if new_status in _LEFT_STATUSES and old_status not in _LEFT_STATUSES:
        await reverse_referral_on_leave(user, group_id)
        return

    # Только новые вступления
    if new_status != "member" or old_status not in _LEFT_STATUSES:
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
        # Счётчик «добавил в группу» растёт только при реальном начислении,
        # иначе повторное событие от Telegram засчитало бы участника дважды
        if await credit_referral(referrer_id, user.id, user.full_name):
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
