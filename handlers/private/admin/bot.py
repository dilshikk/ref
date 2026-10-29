# handlers/private/admin/bot.py
"""🤖 Бот: тексты, опросы, команды меню."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import (
    bot_kb,
    bot_menu_settings_kb,
    settings_kb,
)
from loader import db
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS, url_yaml
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


@router.message(F.text == "🏠 Главное меню", IsBotAdminFilter(ADMINS))
async def bot_main_menu(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню бота</b>\n\n"
        "Здесь можно управлять текстами, языками, уведомлениями и реферальной логикой.",
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


@router.message(F.text == "🌍 Языки", IsBotAdminFilter(ADMINS))
async def bot_languages_menu(message: types.Message):
    await message.answer(
        "🌍 <b>Языки</b>\n\n"
        "В таблице пользователей уже есть поле language, но админ-управление им ещё не вынесено.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "🎁 Реферальные настройки", IsBotAdminFilter(ADMINS))
async def bot_referral_menu(message: types.Message):
    await message.answer(
        "🎁 <b>Реферальные настройки</b>\n\n"
        f"Сумма за приглашение: <b>{url_yaml.get('invited_credit', 0)}</b>\n"
        f"Антифрод-лимит: <b>{url_yaml.get('antispam_referral_limit', 100)}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.message(F.text == "📢 Уведомления", IsBotAdminFilter(ADMINS))
async def bot_notifications_menu(message: types.Message):
    await message.answer(
        "📢 <b>Уведомления</b>\n\n"
        "Сейчас уведомления администраторам отправляются при старте бота.",
        parse_mode="HTML",
        reply_markup=bot_kb(),
    )


@router.message(F.text == "🗳 Опросы", IsBotAdminFilter(ADMINS))
async def bot_polls_menu(message: types.Message, state: FSMContext):
    default_question = "Насколько вам удобен наш бот?"
    default_options = ["Отлично", "Нормально", "Нужно улучшить"]

    question = str(url_yaml.get("poll_question") or default_question).strip()
    raw_options = url_yaml.get("poll_options")
    if isinstance(raw_options, list):
        options = [str(x).strip() for x in raw_options if str(x).strip()]
    elif isinstance(raw_options, str):
        options = [part.strip() for part in raw_options.split("|") if part.strip()]
    else:
        options = []
    if len(options) < 2:
        options = default_options
    options = options[:10]

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
