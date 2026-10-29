# /www/wwwroot/uz_ibot/handlers/private/profile_settings.py
"""⚙ Настройки пользователя.

#103: кнопка «🌐 Язык» убрана — все тексты бота только на русском, выбор ни на что
не влиял. Колонку language в базе не трогаем: пригодится, если бот переведут.
#104: «👤 Изменить имя» — имя экранируется, не-текст не роняет обработчик,
кнопки меню и команды не сохраняются как имя. Имя видно в /top10 и списках админки.
"""
import html

from aiogram import Router, types, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup
from loguru import logger

from filters import private_only
from keyboards.reply_keyboards import menu_kb, reply_keyboard_for_admin
from loader import db
from states.flows import ReferralFlow
from services.activity import activity_service
from services.audit_log import audit_log_service

router = private_only(Router(name="private.profile_settings"))

_NAME_MIN = 2
_NAME_MAX = 64


def _keyboard_texts(markup: ReplyKeyboardMarkup) -> set[str]:
    return {button.text for row in markup.keyboard for button in row}


def _is_menu_or_command(text: str) -> bool:
    """Нажатая кнопка меню или команда — это не новое имя, а выход из ввода."""
    if text.startswith("/"):
        return True
    menu_texts = _keyboard_texts(menu_kb()) | _keyboard_texts(reply_keyboard_for_admin())
    return text in menu_texts | {"⬅ Назад", "⬅️ Назад", "🔙 Назад"}


@router.message(F.text == "⚙ Настройки")
async def settings_menu(message: types.Message) -> None:
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👤 Изменить имя", callback_data="settings_name")],
    ])
    await message.answer("⚙ <b>Настройки</b>", parse_mode="HTML", reply_markup=markup)
    await activity_service.record(
        user_id=message.from_user.id,
        event="open_settings",
        chat_id=message.chat.id,
        session_id=str(message.message_id),
    )


@router.callback_query(F.data.in_({"settings_lang"}) | F.data.startswith("lang_"))
async def language_removed(call: types.CallbackQuery) -> None:
    # Старые сообщения с кнопкой «🌐 Язык» остались в чатах — отвечаем, а не молчим (#103)
    await call.answer("Бот пока работает только на русском языке.", show_alert=True)


@router.callback_query(F.data == "settings_name")
async def settings_name(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="settings_name_cancel")
    ]])
    await call.message.answer(
        f"👤 Введите новое имя ({_NAME_MIN}–{_NAME_MAX} символов).\n"
        "Оно показывается в /top10.",
        reply_markup=markup,
    )
    await state.set_state(ReferralFlow.CHANGE_NAME)


@router.callback_query(F.data == "settings_name_cancel")
async def settings_name_cancel(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.edit_text("❌ Изменение имени отменено.")


@router.message(ReferralFlow.CHANGE_NAME)
async def save_new_name(message: types.Message, state: FSMContext) -> None:
    # #104: фото/стикер вместо текста раньше роняли обработчик (message.text is None)
    if message.text is None:
        await message.reply("⚠️ Отправьте имя текстом или нажмите «❌ Отмена».")
        return

    new_name = message.text.strip()

    if _is_menu_or_command(new_name):
        # Пользователь нажал кнопку меню — выходим из ввода и отдаём сообщение её обработчику
        await state.clear()
        raise SkipHandler()

    if len(new_name) < _NAME_MIN:
        await message.reply(f"⚠️ Имя слишком короткое. Минимум {_NAME_MIN} символа.")
        return

    if len(new_name) > _NAME_MAX:
        await message.reply(f"⚠️ Имя слишком длинное. Максимум {_NAME_MAX} символа.")
        return

    current_user = await db.select_user(telegram_id=message.from_user.id)
    old_name = current_user["full_name"] if current_user else None
    await db.update_user_name(message.from_user.id, new_name)
    await audit_log_service.record_user_action(
        actor_id=message.from_user.id,
        action="profile_name_changed",
        entity_type="user",
        entity_id=message.from_user.id,
        old_value={"full_name": old_name},
        new_value={"full_name": new_name},
        payload={"source": "settings_name"},
    )
    await activity_service.record(
        user_id=message.from_user.id,
        event="profile_name_changed",
        chat_id=message.chat.id,
        session_id=str(message.message_id),
        payload={"full_name": new_name},
    )
    await state.clear()
    # #104: без экранирования имя с < или & ломало HTML, и Telegram отклонял ответ
    await message.answer(
        f"✅ Имя изменено на <b>{html.escape(new_name)}</b>",
        parse_mode="HTML",
        reply_markup=menu_kb(),
    )
    logger.info(f"Пользователь {message.from_user.id} изменил имя на: {new_name!r}")
