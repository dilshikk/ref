# handlers/private/admin/channels.py
"""📢 Каналы и группы — всё про чаты бота в одном разделе:

- «📌 Обязательные подписки» — добавить/удалить канал (обработчики в handlers/private/subscriptions.py);
- «👥 Группа приглашений» — ссылка и ID группы, куда вступают приглашённые;
- «📝 Группа логов» — куда бот пересылает действия пользователей.

Убраны дубли: «📋 Все» и «📈 Статистика» (тот же список — в «👁‍🗨 Посмотреть каналы»
и «📊 Статистика» → «📢 Каналы»), «⚙️ Настройки каналов» из «⚙️ Настройки»,
«👤 Участников для вывода» (теперь «⚙️ Настройки» → «💸 Выплаты»),
«✏️ Изменить ссылку (URL)» (теперь «⚙️ Настройки» → «🗳 Опрос»).

Значения хранятся в bot_settings (#113, #116).
"""
import html
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from filters import private_only
from keyboards.reply_keyboards import (
    channels_groups_kb,
    group_settings_kb,
    subscription_kb,
)
from keyboards.inline_keyboards.actions import action_keyboard
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_settings, parse_group_chat_id
from services.settings_store import set_setting

from .common import cancel_kb, forms_router, get_channel_totals, record_setting_change

router = private_only(Router(name="private.admin.channels"))


def _or_unset(value: Any, unset: str) -> Any:
    return value if value not in (None, "") else unset


async def _save(user_id: int, key: str, value: Any) -> None:
    """Сохраняет настройку и пишет изменение в журнал."""
    old_value = await set_setting(key, value, user_id)
    await record_setting_change(user_id, key, old_value, value)


# «⬅️ К каналам» — шаг назад из подписок и группы приглашений
@router.message(F.text.in_({"📢 Каналы и группы", "⬅️ К каналам"}), IsBotAdminFilter(ADMINS))
async def channels_groups_menu(message: types.Message, state: FSMContext):
    await state.clear()
    channels = await get_channel_totals()
    settings = get_settings()
    await message.answer(
        "📢 <b>Каналы и группы</b>\n\n"
        f"📌 Обязательных каналов: <b>{channels['total_channels']}</b>\n"
        f"👥 Группа приглашений: <code>{_or_unset(settings.group_id, 'не задана')}</code>\n"
        f"📝 Группа логов: <code>{_or_unset(settings.log_group_id, 'не задана')}</code>\n\n"
        "Выберите, что настроить.",
        parse_mode="HTML",
        reply_markup=channels_groups_kb(),
    )


@router.message(F.text == "📌 Обязательные подписки", IsBotAdminFilter(ADMINS))
async def force_subscription(message: types.Message):
    await message.answer(
        "📌 <b>Обязательные подписки</b>\n\n"
        "Бот работает для пользователя, только когда он подписан на все эти каналы.",
        parse_mode="HTML",
        reply_markup=subscription_kb(),
    )


@router.message(F.text == "👥 Группа приглашений", IsBotAdminFilter(ADMINS))
async def invite_group_menu(message: types.Message):
    settings = get_settings()
    link = html.escape(str(_or_unset(settings.group_link, "не задана")))
    await message.answer(
        "👥 <b>Группа приглашений</b>\n\n"
        "Сюда вступают приглашённые по реферальным ссылкам. "
        "Бот должен быть администратором этой группы.\n\n"
        f"🔗 Ссылка: <code>{link}</code>\n"
        f"🆔 ID: <code>{_or_unset(settings.group_id, 'не задан')}</code>\n\n"
        "Выберите, что изменить.",
        parse_mode="HTML",
        reply_markup=group_settings_kb(),
    )


@router.message(F.text == "🔗 Ссылка группы", IsBotAdminFilter(ADMINS))
async def edit_subcr_channel(message: types.Message):
    value = _or_unset(get_settings().group_link, "не задана")
    await message.answer(
        f"🔗 Текущая ссылка группы:\n<code>{html.escape(str(value))}</code>\n\nХотите изменить?",
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
        f"Введите новую ссылку группы.\nСейчас: {html.escape(str(value))}\n"
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
        await message.answer("✅ Логи в группу отключены.", reply_markup=channels_groups_kb())
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
        parse_mode="HTML", reply_markup=channels_groups_kb(),
    )
    await state.clear()
