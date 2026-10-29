"""📞 Поддержка: обращение пользователя к админам и ответ админа.

#102: режим обращения раньше никогда не снимался — после отправки бот снова
ставил SupportFlow.waiting_message, и кнопки меню (например «🗳 Голосование»)
и любой текст уходили админам. Теперь:
- после отправки обращения состояние сбрасывается;
- на шаге ввода есть inline-кнопка «❌ Отмена»;
- кнопка меню или команда во время ввода закрывают режим и обрабатываются
  как обычно (SkipHandler передаёт сообщение следующим роутерам).
"""
import html

from aiogram import Router, types, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from loguru import logger

from filters import private_only
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings
from keyboards.reply_keyboards import menu_kb, reply_keyboard_for_admin
from states.flows import SupportFlow, AdminFlow

router = private_only(Router(name="private.support"))

_CANCEL_CALLBACK = "support_cancel"

_cancel_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=_CANCEL_CALLBACK)]]
)


def _keyboard_texts(*keyboards: ReplyKeyboardMarkup) -> frozenset[str]:
    return frozenset(button.text for kb in keyboards for row in kb.keyboard for button in row)


# Тексты кнопок меню: такое сообщение — не обращение, а нажатие кнопки
_MENU_TEXTS = _keyboard_texts(menu_kb(), reply_keyboard_for_admin()) | {"⬅ Назад", "⬅️ Назад", "🔙 Назад"}


def is_menu_or_command(text: str | None) -> bool:
    if not text:
        return False
    return text in _MENU_TEXTS or text.startswith("/")


def _support_preview(message: types.Message) -> str:
    if message.text:
        return message.text[:700]
    if message.caption:
        return message.caption[:700]
    if message.photo:
        return "[Фото]"
    if message.video:
        return "[Видео]"
    if message.document:
        return "[Документ]"
    if message.audio:
        return "[Аудио]"
    if message.voice:
        return "[Голосовое]"
    if message.sticker:
        return "[Стикер]"
    return "[Неподдерживаемый формат сообщения]"


@router.message(F.text == "📞 Поддержка")
async def support(message: types.Message, state: FSMContext) -> None:
    support_link = get_settings().support
    extra = f"\n\nТакже можно написать напрямую: {support_link}" if support_link else ""
    await message.answer(
        "📞 <b>Служба поддержки</b>\n\n"
        "Напишите ваш вопрос одним сообщением.\n"
        "Сообщение будет отправлено администраторам, и ответ придет сюда в чат с ботом."
        f"{extra}",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(SupportFlow.waiting_message)


@router.callback_query(F.data == _CANCEL_CALLBACK)
async def support_cancel(call: types.CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() == SupportFlow.waiting_message.state:
        await state.clear()
    if isinstance(call.message, types.Message):
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug(f"support: не удалось убрать кнопку отмены: {e}")
        await call.message.answer("❌ Обращение отменено.")
    await call.answer()


@router.message(SupportFlow.waiting_message)
async def support_send_to_admins(message: types.Message, state: FSMContext) -> None:
    if is_menu_or_command(message.text):
        # Это нажатие кнопки меню или команда, а не обращение: выходим из режима
        # и отдаём сообщение обработчикам в следующих роутерах (обработчик —
        # последний в этом роутере, поэтому SkipHandler уходит дальше).
        await state.clear()
        raise SkipHandler()

    user = message.from_user
    if user is None:
        return
    bot = message.bot
    if bot is None:
        await message.answer("⚠️ Внутренняя ошибка: bot недоступен.")
        return

    preview = _support_preview(message)
    username = f"@{html.escape(user.username)}" if user.username else "-"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✉️ Ответить", callback_data=f"support_reply_{user.id}")]]
    )

    delivered = 0
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                "📨 <b>Новое обращение в поддержку</b>\n\n"
                f"👤 Пользователь: <b>{html.escape(user.full_name)}</b>\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"👤 Username: {username}\n"
                f"📝 Превью: <code>{html.escape(preview)}</code>",
                parse_mode="HTML",
                reply_markup=kb,
            )
            try:
                await message.copy_to(admin_id)
            except Exception as copy_error:
                logger.warning(f"support: не удалось скопировать оригинал обращения админу {admin_id}: {copy_error}")
            delivered += 1
        except Exception as e:
            logger.warning(f"support: не удалось отправить обращение админу {admin_id}: {e}")

    if delivered == 0:
        # Состояние оставляем: можно отправить ещё раз или нажать «Отмена»
        await message.answer(
            "⚠️ Не удалось передать обращение администраторам. Попробуйте позже.",
            reply_markup=_cancel_kb,
        )
        return

    await state.clear()
    await message.answer(
        "✅ Ваше обращение отправлено администрации.\n"
        "Ответ придёт сюда. Чтобы написать ещё — снова нажмите «📞 Поддержка».",
        reply_markup=menu_kb(),
    )


@router.callback_query(F.data.startswith("support_reply_"), IsBotAdminFilter(ADMINS))
async def support_reply_start(call: types.CallbackQuery, state: FSMContext) -> None:
    if not call.data:
        return
    user_id_str = call.data.replace("support_reply_", "", 1)
    if not user_id_str.isdigit():
        await call.answer("Неверный ID пользователя", show_alert=False)
        return

    target_user_id = int(user_id_str)
    await state.update_data(support_target_user_id=target_user_id)
    await state.set_state(AdminFlow.support_reply_input)

    if call.message is not None:
        await call.message.answer(
            "✍️ Введите ответ пользователю.\n"
            "Можно отправить текст, фото, видео, документ, аудио или голосовое.",
        )
    await call.answer()


@router.message(AdminFlow.support_reply_input, IsBotAdminFilter(ADMINS))
async def support_reply_send(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = int(data.get("support_target_user_id") or 0)
    bot = message.bot
    if bot is None:
        await message.answer("⚠️ Внутренняя ошибка: bot недоступен.")
        await state.clear()
        return
    if target_user_id <= 0:
        await message.answer("⚠️ Не найден получатель. Нажмите «Ответить» снова.")
        await state.clear()
        return

    try:
        if message.text:
            await bot.send_message(
                target_user_id,
                f"💬 <b>Ответ поддержки</b>\n\n{message.text}",
                parse_mode="HTML",
            )
        else:
            await bot.send_message(target_user_id, "💬 <b>Ответ поддержки</b>", parse_mode="HTML")
            await message.copy_to(target_user_id)
    except Exception as e:
        logger.warning(f"support: не удалось отправить ответ пользователю {target_user_id}: {e}")
        await message.answer("❌ Не удалось отправить ответ пользователю.")
        await state.clear()
        return

    await message.answer("✅ Ответ отправлен пользователю.")
    await state.clear()
