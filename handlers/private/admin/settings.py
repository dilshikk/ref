# handlers/private/admin/settings.py
"""⚙️ Настройки — единственное место, где меняются суммы, лимиты и чат выплат.

Раньше одна и та же настройка редактировалась в нескольких разделах: лимит
рефералов в час — в «📊 Лимиты», «🎁 Реферальные настройки» и «🔒 Безопасность»,
сумма за приглашение — в «💰 Тарифы» и «🎁 Реферальные настройки», участников
для вывода — в «📊 Лимиты» и «⚙️ Настройки каналов». Теперь каждое число есть
ровно в одной панели (_FIELDS), это проверяет tests/test_limits_panel.py:

- «💸 Выплаты» — мин./макс. сумма вывода, участников для вывода;
- «🎁 Награды» — за приглашение и за голос;
- «🛡 Антифрод» — лимиты рефералов, снятие при выходе, проверка добавленных;
- «🆔 Чат выплат» — чат отчётов и публичная ссылка (#107).

Тексты, опрос и команды меню — в bot.py, группы и каналы — в channels.py.
Значения хранятся в bot_settings (#113): читаем через get_settings() / get_raw(),
сохраняем через set_setting().
"""
import html
from typing import Any, cast
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from filters import private_only
from keyboards.reply_keyboards import settings_kb
from states.flows import AdminFlow
from filters.admin import IsBotAdminFilter
from data.config import ADMINS
from data.settings import get_raw, get_settings, is_http_url
from services.settings_store import set_setting
from utils.payout_report import parse_payment_chat_id

from .common import cancel_kb, forms_router, record_setting_change

router = private_only(Router(name="private.admin.settings"))


async def _save(user_id: int, key: str, value: Any) -> None:
    """Сохраняет настройку и пишет изменение в журнал."""
    old_value = await set_setting(key, value, user_id)
    await record_setting_change(user_id, key, old_value, value)


_OVERVIEW = (
    "⚙️ <b>Настройки</b>\n\n"
    "Каждая настройка находится только здесь — в одном месте.\n\n"
    "💸 Выплаты — суммы вывода и сколько участников добавить в группу.\n"
    "🎁 Награды — сколько платить за приглашение и за голос.\n"
    "🛡 Антифрод — лимиты рефералов и проверка добавленных.\n"
    "🆔 Чат выплат — куда бот пишет отчёты, ссылка для пользователей.\n"
    "📝 Тексты — приветствие, помощь, поддержка.\n"
    "🗳 Опрос — вопрос, варианты и ссылка «Проголосовать».\n"
    "⚙ Команды меню — команды бота в Telegram."
)


@router.message(F.text.in_({"⚙️ Настройки", "⬅️ К настройкам"}), IsBotAdminFilter(ADMINS))
async def settings_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(_OVERVIEW, parse_mode="HTML", reply_markup=settings_kb())


# ─────────────────────────────────────────────
# Числовые настройки: каждый ключ — ровно в одной панели.
# Ключи совпадают с полями Settings и ключами bot_settings.
# ─────────────────────────────────────────────
_FIELDS: dict[str, dict[str, Any]] = {
    "min_balance": {"panel": "payouts", "label": "Мин. сумма вывода", "unit": "сум", "minimum": 0},
    "max_withdraw": {"panel": "payouts", "label": "Макс. сумма вывода", "unit": "сум", "minimum": 1},
    "group_member_count": {"panel": "payouts", "label": "Участников для вывода", "unit": "чел.", "minimum": 1},
    "invited_credit": {"panel": "rewards", "label": "За приглашение", "unit": "сум", "minimum": 0},
    "vote_credit": {"panel": "rewards", "label": "За голос в опросе", "unit": "сум", "minimum": 0},
    "antispam_referral_limit": {"panel": "antifraud", "label": "Рефералов в час", "unit": "чел.", "minimum": 1},
    "antispam_referral_daily_limit": {
        "panel": "antifraud", "label": "Рефералов в сутки", "unit": "чел.", "minimum": 1,
    },
    "referral_hold_hours": {
        "panel": "antifraud", "label": "Снятие при выходе в течение", "unit": "ч.", "minimum": 0,
    },
    "add_min_hours": {"panel": "antifraud", "label": "Добавленный в группе не меньше", "unit": "ч.", "minimum": 0},
    "add_min_score": {"panel": "antifraud", "label": "Мин. балл активности добавленного", "unit": "", "minimum": 0},
}

_PANELS: dict[str, dict[str, str]] = {
    "payouts": {
        "title": "💸 <b>Выплаты</b>",
        "hint": (
            "• Макс. сумма 9999999 — фактически без ограничения.\n"
            "• Участников для вывода — сколько человек нужно добавить в группу перед выводом."
        ),
    },
    "rewards": {
        "title": "🎁 <b>Награды</b>",
        "hint": "• 0 — награда не начисляется.",
    },
    "antifraud": {
        "title": "🛡 <b>Антифрод</b>",
        "hint": (
            "• Вступления сверх лимита не теряются — они ждут решения в «💰 Финансы» → «⏸ На удержании».\n"
            "• «Снятие при выходе»: 0 — начисление снимается при любом выходе из группы.\n"
            "• Часы и балл добавленного: оба 0 — проверка добавленных выключена."
        ),
    },
}

# Текст кнопки → панель. Отдельная константа: по ней тест находит обработчик кнопок.
_PANEL_BY_BUTTON: dict[str, str] = {
    "💸 Выплаты": "payouts",
    "🎁 Награды": "rewards",
    "🛡 Антифрод": "antifraud",
}

_cancel_inline_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="set_edit_cancel")]]
)


def _value(key: str) -> int:
    return int(getattr(get_settings(), key))


def _field_line(key: str) -> str:
    field = _FIELDS[key]
    return f"{field['label']}: <b>{_value(key)}</b> {field['unit']}".rstrip()


def _panel_keys(panel: str) -> list[str]:
    return [key for key, field in _FIELDS.items() if field["panel"] == panel]


def _panel_text(panel: str) -> str:
    info = _PANELS[panel]
    lines = "\n".join(_field_line(key) for key in _panel_keys(panel))
    return f"{info['title']}\n\n{lines}\n\n{info['hint']}\n\nВыберите параметр и отправьте новое число."


def _panel_kb(panel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ {_FIELDS[key]['label']}", callback_data=f"set_edit:{key}")]
        for key in _panel_keys(panel)
    ])


@router.message(F.text.in_(_PANEL_BY_BUTTON), IsBotAdminFilter(ADMINS))
async def settings_panel(message: types.Message):
    panel = _PANEL_BY_BUTTON.get(message.text or "")
    if panel is None:
        return
    await message.answer(_panel_text(panel), parse_mode="HTML", reply_markup=_panel_kb(panel))


@router.callback_query(F.data.startswith("set_edit:"), IsBotAdminFilter(ADMINS))
async def settings_edit_callback(call: types.CallbackQuery, state: FSMContext):
    if call.message is None or not call.data:
        return
    key = call.data.split(":", 1)[1]
    field = _FIELDS.get(key)
    if field is None:
        await call.answer("Неизвестный параметр")
        return
    await state.update_data(
        setting_key=key,
        panel_chat_id=call.message.chat.id,
        panel_message_id=call.message.message_id,
    )
    await state.set_state(AdminFlow.set_limit_value)
    await cast(Any, call.message).answer(
        f"Введите новое значение для «{field['label']}» (целое число, не меньше {field['minimum']}).\n"
        f"Сейчас: <b>{_value(key)}</b>",
        parse_mode="HTML",
        reply_markup=_cancel_inline_kb,
    )
    await call.answer()


@router.callback_query(F.data == "set_edit_cancel", IsBotAdminFilter(ADMINS))
async def settings_edit_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if call.message is not None:
        await cast(Any, call.message).edit_reply_markup(reply_markup=None)
    await call.answer("Редактирование отменено")


@forms_router.message(AdminFlow.set_limit_value, IsBotAdminFilter(ADMINS))
async def settings_value_input(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    field = _FIELDS.get(key)
    if field is None:
        await message.answer("⚠️ Сессия истекла. Откройте нужный раздел в «⚙️ Настройки» снова.",
                             reply_markup=settings_kb())
        await state.clear()
        return

    raw = (message.text or "").strip()
    minimum = int(field["minimum"])
    if not raw.isdigit() or int(raw) < minimum:
        await message.answer(f"❌ Введите целое число не меньше {minimum}.", reply_markup=_cancel_inline_kb)
        return

    await _save(user.id, key, int(raw))

    # Обновляем исходную панель, чтобы админ сразу видел новое значение
    panel = str(field["panel"])
    chat_id = data.get("panel_chat_id")
    message_id = data.get("panel_message_id")
    if message.bot is not None and isinstance(chat_id, int) and isinstance(message_id, int):
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_panel_text(panel),
                parse_mode="HTML",
                reply_markup=_panel_kb(panel),
            )
        except Exception:
            await message.answer(_panel_text(panel), parse_mode="HTML", reply_markup=_panel_kb(panel))

    await message.answer(f"✅ {_field_line(key)}", parse_mode="HTML", reply_markup=settings_kb())
    await state.clear()


# ─────────────────────────────────────────────
# 🆔 Чат выплат: чат отчётов (payment_chat_id) и публичная ссылка (payment_channel, #107).
# Перенесён сюда из «💰 Финансы»: это настройка, а не операция с деньгами.
# ─────────────────────────────────────────────
def _payment_chat_id_text() -> str:
    value = get_raw("payment_chat_id")
    return str(value) if value not in (None, "") else "не задан"


def _payment_chat_text() -> str:
    link = get_settings().payment_channel or "не задана"
    return (
        "🆔 <b>Чат выплат</b>\n\n"
        f"🤖 Чат для отчётов о выплатах (сюда пишет бот):\n<code>{html.escape(_payment_chat_id_text())}</code>\n\n"
        "👥 Публичная ссылка для пользователей (кнопка «📢 Чат выплат»):\n"
        f"{html.escape(link)}\n\n"
        "Что изменить?"
    )


def _payment_chat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Чат отчётов", callback_data="yes_payment_chat_id")],
        [InlineKeyboardButton(text="👥 Ссылка для пользователей", callback_data="payment_channel_edit")],
        # «no» обрабатывает admin/menu.py::cancel_edit
        [InlineKeyboardButton(text="❌ Ничего", callback_data="no")],
    ])


@router.message(F.text == "🆔 Чат выплат", IsBotAdminFilter(ADMINS))
async def edit_payment_chat_id(message: types.Message):
    await message.answer(_payment_chat_text(), parse_mode="HTML", reply_markup=_payment_chat_kb())


@router.callback_query(F.data == "yes_payment_chat_id", IsBotAdminFilter(ADMINS))
async def confirm_payment_chat_id(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    await cast(Any, msg).answer(
        "Введите новый чат для отчётов о выплатах.\n"
        f"Сейчас: {html.escape(_payment_chat_id_text())}\n"
        "Примеры: <code>@my_channel</code> или <code>-1001234567890</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_payment_chat_id)


@forms_router.message(AdminFlow.set_payment_chat_id, IsBotAdminFilter(ADMINS))
async def update_payment_chat_id(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    parsed = parse_payment_chat_id(text)
    if parsed is None:
        await message.answer(
            "❌ Неверный формат!\n"
            "Используйте <code>@channel_username</code> или <code>-1001234567890</code>.",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    await _save(user.id, "payment_chat_id", parsed)
    await message.answer(
        f"✅ Чат отчётов обновлён: <code>{html.escape(str(parsed))}</code>",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )
    await state.clear()


@router.callback_query(F.data == "payment_channel_edit", IsBotAdminFilter(ADMINS))
async def confirm_payment_channel(call: types.CallbackQuery, state: FSMContext):
    msg = call.message
    if msg is None:
        return
    await call.answer()
    await cast(Any, msg).answer(
        "Введите публичную ссылку на чат/канал выплат — её увидят пользователи по кнопке «📢 Чат выплат».\n"
        "Пример: <code>https://t.me/my_payouts</code>\n\n"
        "Отправьте <code>-</code>, чтобы убрать ссылку.",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AdminFlow.set_payment_channel)


@forms_router.message(AdminFlow.set_payment_channel, IsBotAdminFilter(ADMINS))
async def update_payment_channel(message: types.Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if text != "-" and not is_http_url(text):
        await message.answer(
            "❌ Нужна ссылка, начинающаяся с https://\nПример: <code>https://t.me/my_payouts</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        return

    new_value = "" if text == "-" else text
    await _save(user.id, "payment_channel", new_value)
    result = f"✅ Ссылка обновлена: {html.escape(new_value)}" if new_value else "✅ Ссылка убрана."
    await message.answer(result, parse_mode="HTML", reply_markup=settings_kb())
    await state.clear()
