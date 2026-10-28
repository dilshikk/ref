# /www/wwwroot/uz_ibot/handlers/private/profile_settings.py
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from loguru import logger

from filters import private_only
from loader import db
from states.flows import ReferralFlow
from services.activity import activity_service
from services.audit_log import audit_log_service

router = private_only(Router(name="private.profile_settings"))

_LANG_NAMES: dict[str, str] = {
    "ru": "🇷🇺 Русский",
    "uz": "🇺🇿 O'zbek",
}


@router.message(F.text == "⚙ Настройки")
async def settings_menu(message: types.Message) -> None:
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🌐 Язык", callback_data="settings_lang")],
        [types.InlineKeyboardButton(text="👤 Изменить имя", callback_data="settings_name")],
    ])
    await message.answer("⚙ <b>Настройки</b>", parse_mode="HTML", reply_markup=markup)
    await activity_service.record(
        user_id=message.from_user.id,
        event="open_settings",
        chat_id=message.chat.id,
        session_id=str(message.message_id),
    )


@router.callback_query(F.data == "settings_lang")
async def settings_lang(call: types.CallbackQuery) -> None:
    await call.answer()

    # Показываем текущий язык
    current_lang = await db.get_user_language(call.from_user.id)

    buttons = []
    for code, label in _LANG_NAMES.items():
        tick = " ✅" if code == current_lang else ""
        buttons.append([
            types.InlineKeyboardButton(
                text=f"{label}{tick}",
                callback_data=f"lang_{code}",
            )
        ])

    await call.message.edit_text(
        "🌐 <b>Выберите язык:</b>",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("lang_"))
async def set_language(call: types.CallbackQuery) -> None:
    await call.answer()
    lang = call.data.split("_")[1]

    if lang not in _LANG_NAMES:
        await call.answer("⚠️ Неизвестный язык.", show_alert=True)
        return

    old_lang = await db.get_user_language(call.from_user.id)
    await db.set_user_language(call.from_user.id, lang)
    await audit_log_service.record_user_action(
        actor_id=call.from_user.id,
        action="language_changed",
        entity_type="user",
        entity_id=call.from_user.id,
        old_value={"language": old_lang},
        new_value={"language": lang},
        payload={"source": "settings_lang"},
    )
    await activity_service.record(
        user_id=call.from_user.id,
        event="language_changed",
        chat_id=call.message.chat.id if call.message else None,
        session_id=call.id,
        payload={"language": lang},
    )
    logger.info(f"Пользователь {call.from_user.id} сменил язык на: {lang}")

    await call.message.edit_text(
        f"✅ Язык изменён на <b>{_LANG_NAMES[lang]}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings_name")
async def settings_name(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="settings_name_cancel")
    ]])
    await call.message.answer("👤 Введите новое имя:", reply_markup=markup)
    await state.set_state(ReferralFlow.CHANGE_NAME)


@router.callback_query(F.data == "settings_name_cancel")
async def settings_name_cancel(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.edit_text("❌ Изменение имени отменено.")


@router.message(ReferralFlow.CHANGE_NAME)
async def save_new_name(message: types.Message, state: FSMContext) -> None:
    new_name = message.text.strip()

    if len(new_name) < 2:
        await message.reply("⚠️ Имя слишком короткое. Минимум 2 символа.")
        return

    if len(new_name) > 64:
        await message.reply("⚠️ Имя слишком длинное. Максимум 64 символа.")
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
    await message.answer(
        f"✅ Имя изменено на <b>{new_name}</b>",
        parse_mode="HTML",
    )
    logger.info(f"Пользователь {message.from_user.id} изменил имя на: {new_name!r}")
