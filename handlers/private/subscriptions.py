# /www/wwwroot/uz_ibot/handlers/private/subscriptions.py
# /www/wwwroot/uz_ibot/handlers/private/subscriptions.py
import asyncio

from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from filters import IsBotAdmin, private_only
from keyboards.reply_keyboards import reply_keyboard_for_admin
from loader import db, bot
from states.flows import ChannelAdminFlow
from services.audit_log import audit_log_service

router = private_only(Router(name="private.subscriptions"))

# Статусы при которых бот считается "в канале"
_BOT_VALID_STATUSES = ("administrator", "member", "creator")


# 🔙 Назад в главное меню
@router.message(F.text.in_({"🔙 Назад", "⬅️ Назад"}), IsBotAdmin())
async def go_back(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


# ➕ Добавить новый канал
@router.message(F.text == "➕ Добавить канал", IsBotAdmin())
async def add_channel(message: types.Message, state: FSMContext) -> None:
    await message.answer(
        "Введите <b>chat_id</b> Telegram-канала или группы (например: <code>-1001234567890</code>):",
        parse_mode="HTML",
    )
    await state.set_state(ChannelAdminFlow.waiting_for_chat_id)


# 🔍 Проверка и добавление канала
@router.message(ChannelAdminFlow.waiting_for_chat_id)
async def process_chat_id(message: types.Message, state: FSMContext) -> None:
    chat_id = message.text.strip()

    if not (chat_id.startswith("-100") and chat_id[1:].isdigit()):
        await message.answer(
            "❌ Неверный формат chat_id.\n"
            "Пример: <code>-1001234567890</code>\n\nПопробуйте снова 👇",
            parse_mode="HTML",
        )
        return

    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_id), timeout=5)
        title = chat.title or "Без названия"

        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status not in _BOT_VALID_STATUSES:  # ✅ добавлен "creator"
            await message.answer("🚫 Бот не состоит в этом канале. Добавьте бота и попробуйте снова.")
            await state.clear()
            return

    except asyncio.TimeoutError:  # ✅ явно ловим таймаут
        logger.warning(f"Таймаут при получении данных канала {chat_id}")
        await message.answer("⏱ Telegram не ответил вовремя. Попробуйте снова.")
        await state.clear()
        return
    except TelegramForbiddenError:
        await message.answer("🚫 Бот не состоит в этом канале. Добавьте бота и попробуйте снова.")
        await state.clear()
        return
    except TelegramBadRequest:
        await message.answer("❌ Канал с таким ID не найден.")
        await state.clear()
        return
    except Exception as e:
        logger.error(f"Ошибка при получении данных канала {chat_id}: {e}")
        await message.answer(f"⚠️ Ошибка:\n<code>{e}</code>", parse_mode="HTML")
        await state.clear()
        return

    # Проверка дубликатов
    existing_channels = await db.get_all_channels()
    if any(str(ch["chat_id"]) == str(chat_id) for ch in existing_channels):
        await message.answer(f"⚠️ Канал <code>{chat_id}</code> уже есть в базе.", parse_mode="HTML")
        await state.clear()
        return

    if chat.username:
        url = f"https://t.me/{chat.username}"
        await db.add_channel(chat_id, url)
        await audit_log_service.record_admin_action(
            actor_id=message.from_user.id,
            action="channel_added",
            entity_type="channel",
            entity_id=str(chat_id),
            new_value={"chat_id": chat_id, "link": url, "title": title},
        )
        await message.answer(
            f"✅ Канал добавлен!\n\n"
            f"📢 <b>Название:</b> {title}\n"
            f"🆔 <b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"🔗 <b>Ссылка:</b> {url}",
            parse_mode="HTML",
        )
        await state.clear()
    else:
        await state.update_data(chat_id=chat_id, title=title)
        await message.answer(
            f"📢 <b>{title}</b> — приватный канал.\n\n"
            "Отправьте инвайт-ссылку для вступления:\n"
            "Пример: <code>https://t.me/+XXXXXXXXXX</code>\n\n"
            "Создать ссылку: Настройки канала → Пригласительные ссылки",
            parse_mode="HTML",
        )
        await state.set_state(ChannelAdminFlow.waiting_for_url)


@router.message(ChannelAdminFlow.waiting_for_url)
async def process_channel_url(message: types.Message, state: FSMContext) -> None:
    url = message.text.strip()

    is_public = (
        url.startswith("https://t.me/")
        and not url.startswith("https://t.me/+")
        and " " not in url
    )
    is_invite = url.startswith("https://t.me/+") and " " not in url

    if not (is_public or is_invite):
        await message.answer(
            "❌ Неверный формат ссылки.\n\n"
            "Допустимые форматы:\n"
            "• <code>https://t.me/username</code> — публичный\n"
            "• <code>https://t.me/+XXXXXXXXXX</code> — приватный (инвайт)",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    chat_id = data["chat_id"]
    title = data["title"]

    await db.add_channel(chat_id, url)
    await audit_log_service.record_admin_action(
        actor_id=message.from_user.id,
        action="channel_added",
        entity_type="channel",
        entity_id=str(chat_id),
        new_value={"chat_id": chat_id, "link": url, "title": title},
    )
    await message.answer(
        f"✅ Канал добавлен!\n\n"
        f"📢 <b>Название:</b> {title}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"🔗 <b>Ссылка:</b> {url}",
        parse_mode="HTML",
    )
    await state.clear()


# ➖ Удалить канал
@router.message(F.text == "➖ Удалить канал", IsBotAdmin())
async def delete_channel(message: types.Message, state: FSMContext) -> None:
    channels = await db.get_all_channels()

    if not channels:
        await message.answer("⚠️ В базе пока нет добавленных каналов для удаления.")
        return

    channel_list = "\n".join([f"• <code>{ch['chat_id']}</code>" for ch in channels[:10]])
    await message.answer(
        f"📋 Найдено {len(channels)} канал(ов) в базе.\n"
        f"Введите <b>chat_id</b> канала, который хотите удалить:\n\n"
        f"{channel_list}",
        parse_mode="HTML",
    )
    await state.set_state(ChannelAdminFlow.waiting_for_delete_id)


@router.message(ChannelAdminFlow.waiting_for_delete_id)
async def confirm_delete_channel(message: types.Message, state: FSMContext) -> None:
    chat_id = message.text.strip()
    channels = await db.get_all_channels()

    target = next((ch for ch in channels if str(ch["chat_id"]) == chat_id), None)
    if not target:
        await message.answer(
            f"❌ Канал с ID <code>{chat_id}</code> не найден в базе.",
            parse_mode="HTML",
        )
        await state.clear()
        return

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{chat_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete"),
    ]])

    await message.answer(
        f"Вы уверены, что хотите удалить канал?\n\n"
        f"🆔 <code>{chat_id}</code>\n🔗 {target['link']}",
        parse_mode="HTML",
        reply_markup=confirm_keyboard,
    )


# ✅ Подтверждение удаления
@router.callback_query(F.data.startswith("confirm_delete_"))
async def delete_confirmed(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    chat_id = call.data.split("_")[-1]
    existing = next((ch for ch in await db.get_all_channels() if str(ch["chat_id"]) == chat_id), None)
    await db.delete_channel(chat_id)
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="channel_deleted",
        entity_type="channel",
        entity_id=chat_id,
        old_value={"chat_id": existing["chat_id"], "link": existing["link"]} if existing else {"chat_id": chat_id},
        new_value=None,
    )
    await call.message.edit_text(
        f"🗑 Канал с ID <code>{chat_id}</code> успешно удалён.",
        parse_mode="HTML",
    )
    await state.clear()


# ❌ Отмена удаления
@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()  # ✅ убираем спиннер
    await call.message.edit_text("❌ Удаление отменено.")
    await state.clear()


# 👁 Список обязательных каналов
@router.message(F.text == "👁‍🗨 Посмотреть каналы", IsBotAdmin())
async def list_channels(message: types.Message) -> None:
    channels = await db.get_all_channels()

    if not channels:
        await message.answer("⚠️ Каналы пока не добавлены.")
        return

    text_lines = [
        f"{i}. 📢 <b>Chat ID:</b> <code>{ch['chat_id']}</code>\n"
        f"🔗 <a href='{ch['link']}'>Перейти</a>"
        for i, ch in enumerate(channels, start=1)
    ]
    await message.answer(
        "📋 <b>Список каналов:</b>\n\n" + "\n\n".join(text_lines),
        parse_mode="HTML",
    )
