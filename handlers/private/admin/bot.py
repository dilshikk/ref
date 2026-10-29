# handlers/private/admin/bot.py
"""🤖 Бот: тексты, опросы, ссылка голосования, реферальные настройки, команды меню.
Заглушки «🏠 Главное меню», «🌍 Языки», «📢 Уведомления» удалены (#87, #89, #90):
их кнопок нет ни на одной клавиатуре."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import (
    bot_kb,
    bot_menu_settings_kb,
)
from loader import db
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS, url_yaml
from data.settings import get_settings
from utils.yaml_io import save_url_yaml
from utils.bot_commands import ADMIN_COMMANDS, USER_COMMANDS, format_commands, set_all_commands

from .common import cancel_kb, forms_router, record_setting_change, short_text

router = private_only(Router(name="private.admin.bot"))


@router.message(F.text == "🤖 Бот", IsBotAdminFilter(ADMINS))
async def bot_menu(message: types.Message):
    await message.answer(
        "🤖 <b>Бот</b>\n\n"
        "Команды и служебные настройки бота.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "📝 Тексты", IsBotAdminFilter(ADMINS))
async def bot_texts_menu(message: types.Message, state: FSMContext):
    templates = cast(dict[str, Any], url_yaml.get("text_templates") or {})
    start_tpl = short_text(templates.get("start"), 90) if templates.get("start") else "—"
    help_tpl = short_text(templates.get("help"), 90) if templates.get("help") else "—"
    support_tpl = short_text(templates.get("support"), 90) if templates.get("support") else "—"

    await message.answer(
        "📝 <b>Тексты</b>\n\n"
        "Редактор шаблонов уже доступен.\n"
        "Отправьте строку в формате: <code>ключ=текст</code>\n\n"
        "Доступные ключи:\n"
        "• <code>start</code>\n"
        "• <code>help</code>\n"
        "• <code>support</code>\n\n"
        "Текущие значения:\n"
        f"• start: <code>{start_tpl}</code>\n"
        f"• help: <code>{help_tpl}</code>\n"
        f"• support: <code>{support_tpl}</code>\n\n"
        "Пример:\n"
        "<code>support=📞 Для связи: @your_support</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_text_template)


@forms_router.message(AdminFlow.set_text_template, IsBotAdminFilter(ADMINS))
async def update_text_template(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    raw = (message.text or "").strip()
    if "=" not in raw:
        await message.answer(
            "❌ Неверный формат.\n"
            "Используйте: <code>ключ=текст</code>\n"
            "Например: <code>start=Добро пожаловать!</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    key_raw, value_raw = raw.split("=", 1)
    key = key_raw.strip().lower()
    value = value_raw.strip()

    if key not in {"start", "help", "support"}:
        await message.answer(
            "❌ Неизвестный ключ.\n"
            "Доступно: <code>start</code>, <code>help</code>, <code>support</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return
    if not value:
        await message.answer("❌ Текст не должен быть пустым.", reply_markup=cancel_kb)
        return

    templates = cast(dict[str, Any], url_yaml.get("text_templates") or {})
    old_value = templates.get(key)
    templates[key] = value
    url_yaml["text_templates"] = templates
    save_url_yaml()

    await record_setting_change(
        actor_id=user.id,
        setting_name=f"text_templates.{key}",
        old_value=old_value,
        new_value=value,
    )

    await message.answer(
        f"✅ Шаблон <code>{key}</code> обновлён.\n\n"
        f"Новое значение:\n<code>{short_text(value, 300)}</code>",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )
    await state.clear()


# ─────────────────────────────────────────────
# 🎁 Реферальные настройки (#85): раньше только показ, теперь инлайн-панель
# по образцу «💰 Тарифы». Ключи совпадают с полями Settings и ключами url.yml.
# ─────────────────────────────────────────────
_REFERRAL_FIELDS: dict[str, dict[str, Any]] = {
    "invited_credit": {"label": "Сумма за приглашение", "unit": "сум", "minimum": 0},
    "antispam_referral_limit": {"label": "Лимит в час", "unit": "чел.", "minimum": 1},
    "antispam_referral_daily_limit": {"label": "Лимит в сутки", "unit": "чел.", "minimum": 1},
    "referral_hold_hours": {"label": "Снятие при выходе в течение", "unit": "ч.", "minimum": 0},
}


def _referral_value(key: str) -> int:
    return int(getattr(get_settings(), key))


def _referral_text() -> str:
    lines = [
        f"{field['label']}: <b>{_referral_value(key)}</b> {field['unit']}"
        for key, field in _REFERRAL_FIELDS.items()
    ]
    return (
        "🎁 <b>Реферальные настройки</b>\n\n"
        + "\n".join(lines)
        + "\n\nВступления сверх лимита не теряются — они ждут решения в «⏸ На удержании».\n"
        "«Снятие при выходе»: 0 — начисление снимается при любом выходе из группы.\n\n"
        "Выберите параметр и отправьте новое число."
    )


def _referral_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ {field['label']}", callback_data=f"ref_edit:{key}")]
        for key, field in _REFERRAL_FIELDS.items()
    ])


def _referral_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="ref_edit_cancel")]]
    )


@router.message(F.text == "🎁 Реферальные настройки", IsBotAdminFilter(ADMINS))
async def bot_referral_menu(message: types.Message):
    # Кнопка есть и в «🤖 Бот», и в «⚙️ Настройки»: reply-клавиатуру не меняем,
    # чтобы админ остался в том разделе, откуда пришёл.
    await message.answer(_referral_text(), parse_mode="HTML", reply_markup=_referral_kb())


@router.callback_query(F.data.startswith("ref_edit:"), IsBotAdminFilter(ADMINS))
async def ref_edit_callback(call: types.CallbackQuery, state: FSMContext):
    if call.message is None or not call.data:
        return
    key = call.data.split(":", 1)[1]
    field = _REFERRAL_FIELDS.get(key)
    if field is None:
        await call.answer("Неизвестный параметр")
        return
    await state.update_data(
        referral_key=key,
        referral_panel_chat_id=call.message.chat.id,
        referral_panel_message_id=call.message.message_id,
    )
    await state.set_state(AdminFlow.set_referral_value)
    await cast(Any, call.message).answer(
        f"Введите новое значение для «{field['label']}» (целое число, не меньше {field['minimum']}).\n"
        f"Сейчас: <b>{_referral_value(key)}</b>",
        parse_mode="HTML",
        reply_markup=_referral_cancel_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ref_edit_cancel", IsBotAdminFilter(ADMINS))
async def ref_edit_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.message is not None:
        await cast(Any, call.message).edit_reply_markup(reply_markup=None)
    await call.answer("Редактирование отменено")


@forms_router.message(AdminFlow.set_referral_value, IsBotAdminFilter(ADMINS))
async def ref_value_input(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    key = str(data.get("referral_key") or "")
    field = _REFERRAL_FIELDS.get(key)
    if field is None:
        await message.answer("⚠️ Сессия истекла. Откройте «🎁 Реферальные настройки» снова.")
        await state.clear()
        return

    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < int(field["minimum"]):
        await message.answer(
            f"❌ Введите целое число не меньше {field['minimum']}.",
            reply_markup=_referral_cancel_kb(),
        )
        return

    value = int(raw)
    old_value = url_yaml.get(key)
    url_yaml[key] = value
    save_url_yaml()
    await record_setting_change(user.id, key, old_value, value)

    # Обновляем исходную панель, чтобы админ сразу видел новое значение
    chat_id = data.get("referral_panel_chat_id")
    message_id = data.get("referral_panel_message_id")
    if message.bot is not None and isinstance(chat_id, int) and isinstance(message_id, int):
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_referral_text(),
                parse_mode="HTML",
                reply_markup=_referral_kb(),
            )
        except Exception:
            await message.answer(_referral_text(), parse_mode="HTML", reply_markup=_referral_kb())

    await message.answer(f"✅ {field['label']}: <b>{value}</b> {field['unit']}", parse_mode="HTML")
    await state.clear()


@router.message(F.text == "🗳 Опросы", IsBotAdminFilter(ADMINS))
async def bot_polls_menu(message: types.Message, state: FSMContext):
    # Вопрос и варианты разбирает get_settings() — та же логика, что при рассылке
    # опроса. Раньше здесь была своя копия разбора с отдельными умолчаниями (#49).
    settings = get_settings()
    question = settings.poll_question
    options = settings.poll_options

    voters = await db.count_poll_voters(question)
    polls_sent = await db.count_poll_instances(question)
    option_rows = cast(list[dict[str, Any]], await db.get_poll_option_stats(question) or [])
    counts = {int(row.get("option_index") or 0): int(row.get("votes") or 0) for row in option_rows}

    lines = [
        f"{idx + 1}) {opt} — <b>{counts.get(idx, 0)}</b>"
        for idx, opt in enumerate(options)
    ]

    await message.answer(
        "🗳 <b>Опросы</b>\n\n"
        f"Текущий вопрос:\n<b>{question}</b>\n\n"
        "Варианты и голоса:\n"
        + "\n".join(lines)
        + "\n\n"
        f"Уникальных голосовавших: <b>{voters}</b>\n"
        f"Опрос отправлен пользователям: <b>{polls_sent}</b> раз\n\n"
        "Чтобы изменить опрос, отправьте одной строкой:\n"
        "<code>Вопрос | Вариант 1 | Вариант 2 | Вариант 3</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_poll_config)


@forms_router.message(AdminFlow.set_poll_config, IsBotAdminFilter(ADMINS))
async def update_poll_config(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    raw = (message.text or "").strip()
    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if len(parts) < 3:
        await message.answer(
            "❌ Неверный формат.\n"
            "Нужно минимум 1 вопрос и 2 варианта:\n"
            "<code>Вопрос | Вариант 1 | Вариант 2</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    question = parts[0]
    options = parts[1:11]
    if len(options) < 2:
        await message.answer("❌ Нужно минимум 2 варианта ответа.", reply_markup=cancel_kb)
        return

    old_question = url_yaml.get("poll_question")
    old_options = url_yaml.get("poll_options")

    url_yaml["poll_question"] = question
    url_yaml["poll_options"] = options
    save_url_yaml()

    await record_setting_change(
        actor_id=user.id,
        setting_name="poll_config",
        old_value={"poll_question": old_question, "poll_options": old_options},
        new_value={"poll_question": question, "poll_options": options},
    )

    await message.answer(
        "✅ Опрос обновлен.\n\n"
        f"Вопрос: <b>{question}</b>\n"
        "Варианты:\n"
        + "\n".join([f"{idx + 1}) {opt}" for idx, opt in enumerate(options)]),
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )
    await state.clear()


@router.message(F.text.in_({"⚙ Меню бота", "⚙️ Меню бота"}), IsBotAdminFilter(ADMINS))
async def bot_menu_settings(message: types.Message):
    await message.answer(
        "⚙ <b>Меню бота</b>\n\n"
        "Здесь можно посмотреть и обновить команды для пользователей и админов.",
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "👥 Команды пользователей", IsBotAdminFilter(ADMINS))
async def show_user_commands(message: types.Message):
    await message.answer(
        "👥 <b>Команды пользователей</b>\n\n" + format_commands(USER_COMMANDS),
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "🛡 Команды админов", IsBotAdminFilter(ADMINS))
async def show_admin_commands(message: types.Message):
    await message.answer(
        "🛡 <b>Команды админов</b>\n\n" + format_commands(ADMIN_COMMANDS),
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )


@router.message(F.text == "🔄 Обновить меню", IsBotAdminFilter(ADMINS))
async def refresh_bot_menu(message: types.Message):
    bot = message.bot
    if bot is None:
        await message.answer("⚠️ Не удалось обновить меню: bot недоступен.")
        return
    await set_all_commands(bot)
    await message.answer(
        "✅ <b>Меню бота обновлено</b>\n\n"
        "Команды пользователей и админов синхронизированы.",
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )
