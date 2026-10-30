# handlers/private/admin/settings.py
"""⚙️ Настройки: ссылка и ID группы, группа логов, участники для вывода, URL голосования.
Заглушки «🔐 Безопасность» (дубль), «🌍 Локализация», «🤖 Поведение бота» удалены (#89, #91, #93).

Значения хранятся в таблице bot_settings (#113): читаем через get_settings() / get_raw(),
сохраняем через set_setting() — база и копия в памяти обновляются сразу, файлов нет."""
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import (
    group_settings_kb,
    settings_kb,
)
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings, parse_group_chat_id
from services.settings_store import set_setting

from .common import cancel_kb, forms_router, record_setting_change

router = private_only(Router(name="private.admin.settings"))

# После сохранения возвращаем меню настроек группы, а не главное меню.


def _or_unset(value: Any, unset: str) -> Any:
    return value if value not in (None, "") else unset


async def _save(user_id: int, key: str, value: Any) -> None:
    """Сохраняет настройку и пишет изменение в журнал."""
    old_value = await set_setting(key, value, user_id)
    await record_setting_change(user_id, key, old_value, value)


@router.message(F.text == "⚙️ Настройки", IsBotAdminFilter(ADMINS))
async def settings_menu(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "💰 Тарифы — суммы вывода и награды.\n"
        "🎁 Реферальные настройки — награда и антифрод.\n"
        "📊 Лимиты — антифрод, удержание и проверка добавленных.\n"
        "⚙️ Настройки каналов — группа и ссылка голосования.",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


# ─────────────────────────────────────────────
# 📊 Лимиты (#92): раньше только показ, дневной лимит, удержание и проверка
# добавленных менялись лишь правкой url.yml. Теперь инлайн-панель по образцу «💰 Тарифы».
# Дубль «📱 Ограничения» в «🔒 Безопасность» удалён.
# ─────────────────────────────────────────────
_LIMIT_FIELDS: dict[str, dict[str, Any]] = {
    "antispam_referral_limit": {"label": "Рефералов в час", "unit": "чел.", "minimum": 1},
    "antispam_referral_daily_limit": {"label": "Рефералов в сутки", "unit": "чел.", "minimum": 1},
    "referral_hold_hours": {"label": "Снятие при выходе в течение", "unit": "ч.", "minimum": 0},
    "group_member_count": {"label": "Участников для вывода", "unit": "чел.", "minimum": 1},
    "add_min_hours": {"label": "Добавленный в группе не меньше", "unit": "ч.", "minimum": 0},
    "add_min_score": {"label": "Мин. балл активности добавленного", "unit": "", "minimum": 0},
}


def _limit_value(key: str) -> int:
    return int(getattr(get_settings(), key))


def _limits_text() -> str:
    lines = [
        f"{field['label']}: <b>{_limit_value(key)}</b> {field['unit']}".rstrip()
        for key, field in _LIMIT_FIELDS.items()
    ]
    return (
        "📊 <b>Лимиты</b>\n\n"
        + "\n".join(lines)
        + "\n\n• Вступления сверх лимита ждут решения в «⏸ На удержании».\n"
        "• «Снятие при выходе»: 0 — начисление снимается при любом выходе из группы.\n"
        "• Часы и балл добавленного: оба 0 — проверка добавленных выключена.\n\n"
        "Выберите параметр и отправьте новое число."
    )


def _limits_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ {field['label']}", callback_data=f"lim_edit:{key}")]
        for key, field in _LIMIT_FIELDS.items()
    ])


def _limits_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="lim_edit_cancel")]]
    )


@router.message(F.text == "📊 Лимиты", IsBotAdminFilter(ADMINS))
async def settings_limits_menu(message: types.Message):
    await message.answer(_limits_text(), parse_mode="HTML", reply_markup=_limits_kb())


@router.callback_query(F.data.startswith("lim_edit:"), IsBotAdminFilter(ADMINS))
async def limit_edit_callback(call: types.CallbackQuery, state: FSMContext):
    if call.message is None or not call.data:
        return
    key = call.data.split(":", 1)[1]
    field = _LIMIT_FIELDS.get(key)
    if field is None:
        await call.answer("Неизвестный параметр")
        return
    await state.update_data(
        limit_key=key,
        limit_panel_chat_id=call.message.chat.id,
        limit_panel_message_id=call.message.message_id,
    )
    await state.set_state(AdminFlow.set_limit_value)
    await cast(Any, call.message).answer(
        f"Введите новое значение для «{field['label']}» (целое число, не меньше {field['minimum']}).\n"
        f"Сейчас: <b>{_limit_value(key)}</b>",
        parse_mode="HTML",
        reply_markup=_limits_cancel_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "lim_edit_cancel", IsBotAdminFilter(ADMINS))
async def limit_edit_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.message is not None:
        await cast(Any, call.message).edit_reply_markup(reply_markup=None)
    await call.answer("Редактирование отменено")


@forms_router.message(AdminFlow.set_limit_value, IsBotAdminFilter(ADMINS))
async def limit_value_input(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    key = str(data.get("limit_key") or "")
    field = _LIMIT_FIELDS.get(key)
    if field is None:
        await message.answer("⚠️ Сессия истекла. Откройте «📊 Лимиты» снова.")
        await state.clear()
        return

    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < int(field["minimum"]):
        await message.answer(
            f"❌ Введите целое число не меньше {field['minimum']}.",
            reply_markup=_limits_cancel_kb(),
        )
        return

    value = int(raw)
    await _save(user.id, key, value)

    # Обновляем исходную панель, чтобы админ сразу видел новое значение
    chat_id = data.get("limit_panel_chat_id")
    message_id = data.get("limit_panel_message_id")
    if message.bot is not None and isinstance(chat_id, int) and isinstance(message_id, int):
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_limits_text(),
                parse_mode="HTML",
                reply_markup=_limits_kb(),
            )
        except Exception:
            await message.answer(_limits_text(), parse_mode="HTML", reply_markup=_limits_kb())

    await message.answer(
        f"✅ {field['label']}: <b>{value}</b> {field['unit']}".rstrip(),
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )
    await state.clear()


@router.message(F.text == "🔗 Ссылка группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_channel(message: types.Message):
    value = _or_unset(get_settings().group_link, "не задана")
    await message.answer(
        f"🔗 Текущая ссылка группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("subcr_channel"),
    )


@router.callback_query(F.data == "yes_subcr_channel", IsBotAdminFilter(ADMINS))
async def confirm_subcr_channel(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    value = _or_unset(get_settings().group_link, "не задана")
    await cast(Any, msg).answer(
        f"Введите новую ссылку группы.\nСейчас: {value}\n"
        "Пример: <code>https://t.me/+XXXXXXXXXX</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_subcr_channel)


@forms_router.message(AdminFlow.set_subcr_channel, IsBotAdminFilter(ADMINS))
async def update_subcr_channel(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("https://t.me/"):
        await message.answer(
            "❌ Неверный формат!\nПример: <code>https://t.me/+XXXXXXXXXX</code>",
            parse_mode="HTML",
        )
        return
    await _save(user.id, "subcr_channel", text)
    await message.answer(
        f"✅ Ссылка группы обновлена!\n🔗 <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


# «🆔 ID группы» — группа реферальных ссылок (раньше GROUP_ID в .env, #116)
@router.message(F.text == "🆔 ID группы", IsBotAdminFilter(ADMINS))
async def edit_group_id(message: types.Message):
    value = _or_unset(get_settings().group_id, "не задан")
    await message.answer(
        f"🆔 Текущий ID группы:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("group_id"),
    )


@router.callback_query(F.data == "yes_group_id", IsBotAdminFilter(ADMINS))
async def confirm_group_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    value = _or_unset(get_settings().group_id, "не задан")
    await cast(Any, msg).answer(
        f"Введите новый chat_id группы.\nСейчас: {value}\n"
        "Пример: <code>-1001234567890</code>\n\n"
        "Бот должен быть администратором этой группы.",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_group_id)


@forms_router.message(AdminFlow.set_group_id, IsBotAdminFilter(ADMINS))
async def update_group_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    chat_id = parse_group_chat_id(message.text or "")
    if chat_id is None:
        await message.answer(
            "❌ Неверный формат!\nПример: <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return
    await _save(user.id, "group_id", chat_id)
    await message.answer(
        f"✅ ID группы обновлён: <code>{chat_id}</code>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


@router.message(F.text == "👤 Участников для вывода", IsBotAdminFilter(ADMINS))
async def edit_group_member_count(message: types.Message):
    value = get_settings().group_member_count
    await message.answer(
        f"👥 Текущее кол-во участников для вывода: <b>{value}</b>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("group_member_count"),
    )


@router.callback_query(F.data == "yes_group_member_count", IsBotAdminFilter(ADMINS))
async def confirm_group_member_count(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        f"Введите новое кол-во участников.\nСейчас: {get_settings().group_member_count}\n"
        "Пример: <code>5</code>",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_group_member_count)


@forms_router.message(AdminFlow.set_group_member_count, IsBotAdminFilter(ADMINS))
async def update_group_member_count(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите целое положительное число!\nПример: <code>5</code>", parse_mode="HTML")
        return
    value = int(text)
    await _save(user.id, "group_member_count", value)
    await message.answer(
        f"✅ Кол-во участников для вывода изменено на <b>{value}</b>.",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


@router.message(F.text == "✏️ Изменить ссылку (URL)", IsBotAdminFilter(ADMINS))
async def edit_url(message: types.Message):
    value = _or_unset(get_settings().vote_url, "не задан")
    await message.answer(
        f"🌐 Текущая ссылка голосования:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("url"),
    )


@router.callback_query(F.data == "yes_url", IsBotAdminFilter(ADMINS))
async def confirm_url_edit(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    value = _or_unset(get_settings().vote_url, "не задан")
    await cast(Any, msg).answer(
        f"Введите новый URL.\nСейчас: {value}",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_url)


@forms_router.message(AdminFlow.set_url, IsBotAdminFilter(ADMINS))
async def update_url(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if not text.startswith("http"):
        await message.answer("❌ Введите корректную ссылку (начиная с http или https):", parse_mode="HTML")
        return
    await _save(user.id, "url", text)
    await message.answer(
        f"✅ URL обновлён: <a href='{text}'>{text}</a>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()


# «📝 Группа логов» — куда бот пересылает действия пользователей (раньше LOG_GROUP_ID в .env, #116)
@router.message(F.text == "📝 Группа логов", IsBotAdminFilter(ADMINS))
async def edit_log_group_id(message: types.Message):
    value = _or_unset(get_settings().log_group_id, "не задана — логи в группу не отправляются")
    await message.answer(
        f"📝 Текущая группа логов:\n<code>{value}</code>\n\nХотите изменить?",
        parse_mode="HTML",
        reply_markup=action_keyboard("log_group_id"),
    )


@router.callback_query(F.data == "yes_log_group_id", IsBotAdminFilter(ADMINS))
async def confirm_log_group_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await cast(Any, msg).answer(
        "Введите chat_id группы для логов.\n"
        "Пример: <code>-1001234567890</code>\n\n"
        "Отправьте <code>-</code>, чтобы отключить логи в группу.\n"
        "Бот должен быть участником этой группы.",
        parse_mode="HTML", reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_log_group_id)


@forms_router.message(AdminFlow.set_log_group_id, IsBotAdminFilter(ADMINS))
async def update_log_group_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if text == "-":
        await _save(user.id, "log_group_id", None)
        await message.answer("✅ Логи в группу отключены.", reply_markup=group_settings_kb())
        await state.clear()
        return
    chat_id = parse_group_chat_id(text)
    if chat_id is None:
        await message.answer(
            "❌ Неверный формат!\nПример: <code>-1001234567890</code> или <code>-</code>, чтобы отключить.",
            parse_mode="HTML",
        )
        return
    await _save(user.id, "log_group_id", chat_id)
    await message.answer(
        f"✅ Группа логов обновлена: <code>{chat_id}</code>",
        parse_mode="HTML", reply_markup=group_settings_kb(),
    )
    await state.clear()
