# handlers/private/admin/bot.py
"""Часть «⚙️ Настройки»: тексты, опрос со ссылкой «Проголосовать», команды меню.

Раздел «🤖 Бот» убран из главного меню: в нём лежали реферальные награды и лимиты,
которые повторяли «💰 Тарифы» и «📊 Лимиты». Числа теперь меняются только в
settings.py, а ссылка «Проголосовать» — только здесь, рядом с опросом
(раньше она была ещё и в «⚙️ Настройки каналов»).

Голос в опросе внутри бота не оплачивается. Если ссылка «Проголосовать» ведёт на буст
канала (https://t.me/boost/канал), бот видит, кто проголосовал, и платит один раз (boosts.py).
При сохранении ссылки бот запоминает ID канала — так канал узнаётся и после смены юзернейма.
"""
import html
from typing import Any, cast
from aiogram import Bot, Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
from filters import private_only
from keyboards.reply_keyboards import (
    bot_menu_settings_kb,
    settings_kb,
)
from loader import db
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import NAME_PLACEHOLDER, TEXT_TEMPLATE_KEYS, get_raw, get_settings
from services.settings_store import set_setting
from utils.boost_link import boost_target
from utils.bot_commands import ADMIN_COMMANDS, USER_COMMANDS, format_commands, set_all_commands

from .common import cancel_kb, forms_router, record_setting_change, short_text

router = private_only(Router(name="private.admin.bot"))

# «ключ=-» сбрасывает шаблон: бот снова показывает текст по умолчанию (#88)
_RESET_VALUE = "-"
# Где показывается каждый шаблон — чтобы админ понимал, что меняет
TEMPLATE_PLACES: dict[str, str] = {
    "start": "приветствие после /start",
    "help": "ответ на /help",
    "support": "текст после кнопки «📞 Поддержка»",
}
# Сколько последних проголосовавших за канал показывать в «🗳 Опрос»
BOOST_LIST_LIMIT = 10


def _texts_overview() -> str:
    # Те же значения, что видит бот (get_settings), экранированные для HTML
    templates = get_settings().text_templates
    lines = []
    for key in TEXT_TEMPLATE_KEYS:
        value = templates.get(key)
        current = f"<code>{html.escape(short_text(value, 90))}</code>" if value else "по умолчанию"
        lines.append(f"• <code>{key}</code> — {TEMPLATE_PLACES[key]}\n   Сейчас: {current}")
    return (
        "📝 <b>Тексты</b>\n\n"
        + "\n".join(lines)
        + "\n\nОтправьте строку: <code>ключ=текст</code>\n"
        f"• <code>{NAME_PLACEHOLDER}</code> заменится на имя пользователя\n"
        f"• <code>ключ={_RESET_VALUE}</code> — вернуть текст по умолчанию\n"
        "• оформление (жирный и т.п.) не поддерживается — текст показывается как есть\n\n"
        "Пример:\n"
        "<code>start=👋 Привет, {name}! Приглашай друзей и зарабатывай.</code>"
    )


@router.message(F.text == "📝 Тексты", IsBotAdminFilter(ADMINS))
async def bot_texts_menu(message: types.Message, state: FSMContext):
    await message.answer(_texts_overview(), parse_mode="HTML", reply_markup=cancel_kb)
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

    if key not in TEXT_TEMPLATE_KEYS:
        await message.answer(
            "❌ Неизвестный ключ.\nДоступно: "
            + ", ".join(f"<code>{k}</code>" for k in TEXT_TEMPLATE_KEYS),
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return
    if not value:
        await message.answer(
            f"❌ Текст не должен быть пустым. Чтобы вернуть стандартный: {key}={_RESET_VALUE}",
            reply_markup=cancel_kb,
        )
        return

    # Копия: кэш настроек меняется только через set_setting (#115)
    stored = get_raw("text_templates")
    templates: dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
    old_value = templates.get(key)
    reset = value == _RESET_VALUE
    if reset:
        templates.pop(key, None)
    else:
        templates[key] = value
    await set_setting("text_templates", templates, user.id)

    await record_setting_change(
        actor_id=user.id,
        setting_name=f"text_templates.{key}",
        old_value=old_value,
        new_value=None if reset else value,
    )

    result = (
        f"✅ Шаблон <code>{key}</code> сброшен — бот показывает текст по умолчанию."
        if reset
        else f"✅ Шаблон <code>{key}</code> обновлён и уже действует.\n\n"
        f"Новое значение:\n<code>{html.escape(short_text(value, 300))}</code>"
    )
    await message.answer(result, parse_mode="HTML", reply_markup=settings_kb())
    await state.clear()


# ─────────────────────────────────────────────
# 🗳 Опрос и ссылка «Проголосовать» — в одном месте
# ─────────────────────────────────────────────
_vote_url_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🔗 Изменить ссылку «Проголосовать»", callback_data="yes_url")]]
)


async def _boost_section(vote_url: str) -> str:
    """Кто проголосовал (буст) за канал по ссылке «Проголосовать»."""
    if boost_target(vote_url) is None:
        return (
            "⚠️ Это не ссылка на буст канала — бот не узнает, кто проголосовал, и не заплатит.\n"
            "Укажите ссылку вида <code>https://t.me/boost/канал</code>."
        )
    stats = await db.boost_reward_stats()
    rows = cast(list[dict[str, Any]], await db.list_recent_boost_rewards(BOOST_LIST_LIMIT) or [])
    lines = []
    for row in rows:
        name = html.escape(row.get("full_name") or "—")
        username = f" @{html.escape(row['username'])}" if row.get("username") else ""
        mark = " ↩️ отменил" if row.get("reversed_at") else ""
        lines.append(f"• {name}{username} — {int(row.get('reward_sum') or 0)} сум{mark} — /uid{row['user_id']}")
    listing = ("\nПоследние:\n" + "\n".join(lines)) if lines else "\nПока никто не проголосовал."
    return (
        f"🚀 Проголосовали за канал и получили награду: <b>{stats['total']}</b> "
        f"(действует сейчас: <b>{stats['active']}</b>)\n"
        f"💰 Награда: <b>{get_settings().boost_credit}</b> сум — меняется в «🎁 Награды»"
        f"{listing}"
    )


@router.message(F.text == "🗳 Опрос", IsBotAdminFilter(ADMINS))
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
        f"{idx + 1}) {html.escape(opt)} — <b>{counts.get(idx, 0)}</b>"
        for idx, opt in enumerate(options)
    ]
    vote_url = settings.vote_url or "не задана"

    await message.answer(
        "🗳 <b>Опрос</b> (внутри бота, не оплачивается)\n\n"
        f"Текущий вопрос:\n<b>{html.escape(question)}</b>\n\n"
        "Варианты и голоса:\n"
        + "\n".join(lines)
        + "\n\n"
        f"Уникальных голосовавших: <b>{voters}</b>\n"
        f"Опрос отправлен пользователям: <b>{polls_sent}</b> раз\n\n"
        "🔗 Ссылка «Проголосовать» (кнопка «🚀 Голос за канал» и ответ, когда бот "
        "не понял сообщение):\n"
        f"<code>{html.escape(vote_url)}</code>\n\n"
        + await _boost_section(settings.vote_url),
        parse_mode="HTML",
        reply_markup=_vote_url_kb,
    )
    await message.answer(
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

    old_question = await set_setting("poll_question", question, user.id)
    old_options = await set_setting("poll_options", options, user.id)

    await record_setting_change(
        actor_id=user.id,
        setting_name="poll_config",
        old_value={"poll_question": old_question, "poll_options": old_options},
        new_value={"poll_question": question, "poll_options": options},
    )

    await message.answer(
        "✅ Опрос обновлен.\n\n"
        f"Вопрос: <b>{html.escape(question)}</b>\n"
        "Варианты:\n"
        + "\n".join([f"{idx + 1}) {html.escape(opt)}" for idx, opt in enumerate(options)]),
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )
    await state.clear()


@router.callback_query(F.data == "yes_url", IsBotAdminFilter(ADMINS))
async def confirm_url_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    value = get_settings().vote_url or "не задана"
    await cast(Any, msg).answer(
        f"Введите новую ссылку «Проголосовать».\nСейчас: {html.escape(value)}\n\n"
        "Чтобы бот видел, кто проголосовал, и платил за голос, укажите ссылку на буст канала:\n"
        "https://t.me/boost/restojobs\n"
        "Бот должен быть администратором этого канала.",
        reply_markup=cancel_kb,
        disable_web_page_preview=True,
    )
    await state.set_state(AdminFlow.set_url)


async def _resolve_boost_chat(bot: Bot | None, url: str, actor_id: int) -> str:
    """
    Запоминает ID канала из ссылки на буст (переживает смену юзернейма).
    Возвращает пояснение для админа.
    """
    target = boost_target(url)
    if target is None:
        await set_setting("boost_chat_id", None, actor_id)
        return "\n\n⚠️ Это не ссылка на буст — бот не узнает, кто проголосовал, и не заплатит."
    if bot is None:
        await set_setting("boost_chat_id", None, actor_id)
        return "\n\n🚀 Это ссылка на буст канала: бот будет платить за голос один раз."
    try:
        chat = await bot.get_chat(target)
    except Exception as e:
        logger.warning(f"Не удалось найти канал {target} для бустов: {e}")
        await set_setting("boost_chat_id", None, actor_id)
        return (
            "\n\n⚠️ Бот не смог найти этот канал. Проверьте ссылку и что бот — администратор канала, "
            "иначе он не увидит голоса."
        )
    await set_setting("boost_chat_id", chat.id, actor_id)
    return (
        "\n\n🚀 Это ссылка на буст канала: бот будет платить за голос один раз.\n"
        "Канал запомнен — смена его юзернейма не помешает учёту голосов "
        "(но ссылку для пользователей тогда нужно обновить)."
    )


@forms_router.message(AdminFlow.set_url, IsBotAdminFilter(ADMINS))
async def update_url(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("http"):
        await message.answer("❌ Введите корректную ссылку (начиная с http или https):", parse_mode="HTML")
        return
    old_value = await set_setting("url", text, user.id)
    await record_setting_change(user.id, "url", old_value, text)
    note = await _resolve_boost_chat(message.bot, text, user.id)
    await message.answer(
        f"✅ Ссылка «Проголосовать» обновлена: {html.escape(text)}{note}",
        parse_mode="HTML", reply_markup=settings_kb(), disable_web_page_preview=True,
    )
    await state.clear()


# ─────────────────────────────────────────────
# ⚙ Команды меню
# ─────────────────────────────────────────────
@router.message(F.text == "⚙ Команды меню", IsBotAdminFilter(ADMINS))
async def bot_menu_settings(message: types.Message):
    await message.answer(
        "⚙ <b>Команды меню</b>\n\n"
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
    # Тот же вызов, что при запуске бота: без списка админов команды /admin,
    # /stats и /holds не появились бы у админов в меню.
    await set_all_commands(bot, admin_ids=ADMINS)
    await message.answer(
        "✅ <b>Меню бота обновлено</b>\n\n"
        "Команды пользователей и админов синхронизированы.",
        parse_mode="HTML",
        reply_markup=bot_menu_settings_kb(),
    )
