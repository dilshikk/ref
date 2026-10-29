# handlers/private/broadcasts.py
"""
Создание и запуск рассылок (#19).

Роутер подключается ДО admin_panel и перехватывает его старые обработчики рассылок:
- можно отправить просто текст, без медиа (раньше такая рассылка «успешно»
  ничего не отправляла); подпись и кнопка теперь необязательны («-» — пропустить);
- «Отправить» больше не шлёт сообщения прямо в обработчике нажатия: создаётся
  задача, которую выполняет services.broadcast_runner с сохранением прогресса;
- в «Запланированных» видны и идущие рассылки — их можно остановить;
- в истории показано, сколько пользователей заблокировали бота;
- блокировка/разблокировка бота пользователем отслеживается (my_chat_member).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from loguru import logger

from data.config import ADMINS
from filters import private_only
from filters.admin import IsBotAdminFilter
from keyboards.reply_keyboards import broadcasts_kb, reply_keyboard_for_admin
from loader import db
from services import broadcast_runner
from services.audit_log import audit_log_service
from states.flows import AdminFlow
from utils.broadcast_send import (
    CAPTION_LIMIT,
    TEXT_LIMIT,
    InvalidBroadcast,
    deliver,
    payload_from_state,
    validate_payload,
)

router = private_only(Router(name="private.broadcasts"))

# my_chat_member не фильтруется private_only (он ставит фильтры только на message/callback)
member_router = Router(name="private.broadcasts.members")
member_router.my_chat_member.filter(F.chat.type == "private")

SCHEDULE_DT_FORMAT = "%Y-%m-%d %H:%M"
_SKIP = "-"
_BACK_TEXTS = {"🔙 Назад", "⬅️ Назад"}

_DRAFT_STATES = (
    AdminFlow.ask_ad_content,
    AdminFlow.ask_ad_caption,
    AdminFlow.ask_ad_url,
    AdminFlow.ask_ad_button_text,
    AdminFlow.ask_ad_schedule_at,
    AdminFlow.are_you_sure,
)

_cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True)

_STATUS_TEXT = {
    "pending": "⏳ Ожидает",
    "running": "🚀 Выполняется",
    "done": "✅ Завершена",
    "failed": "❌ Ошибка",
    "canceled": "🛑 Отменена",
}


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "-")


# ==========================
# Черновик рассылки
# ==========================

@router.message(F.text.in_(_BACK_TEXTS), StateFilter(*_DRAFT_STATES), IsBotAdminFilter(ADMINS))
async def cancel_draft(message: types.Message, state: FSMContext) -> None:
    # Без этого «Назад» на шаге текста ушло бы в рассылку как её текст
    await state.clear()
    await message.answer("🔝 Главное меню", reply_markup=reply_keyboard_for_admin())


@router.message(F.text.in_({"📤 Рассылка", "📤 Создать рассылку", "➕ Новая"}), IsBotAdminFilter(ADMINS))
async def start_ad_creation(message: types.Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    await state.clear()
    await audit_log_service.record_admin_action(
        actor_id=user.id,
        action="broadcast_draft_started",
        entity_type="broadcast",
        entity_id=f"draft:{user.id}",
        payload={"step": "content"},
    )
    await message.answer(
        "📸 Отправьте медиафайл (фото, видео, аудио или документ)\n"
        "или просто <b>текст</b> — тогда рассылка будет текстовой.",
        parse_mode="HTML",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.ask_ad_content)


async def _ask_url(message: types.Message, state: FSMContext) -> None:
    await message.answer(
        "🔗 Отправьте ссылку для кнопки (например: https://t.me/example)\n"
        f"или «{_SKIP}», чтобы отправить без кнопки.",
        reply_markup=_cancel_kb,
    )
    await state.set_state(AdminFlow.ask_ad_url)


@router.message(AdminFlow.ask_ad_content, IsBotAdminFilter(ADMINS))
async def handle_content(message: types.Message, state: FSMContext) -> None:
    if message.text is not None:
        text = message.html_text
        if not message.text.strip():
            await message.answer("⚠️ Текст не должен быть пустым.")
            return
        if message.text.startswith("/"):
            # Команда, набранная по привычке, не должна уйти всем как рассылка
            await message.answer("⚠️ Это похоже на команду. Отправьте текст рассылки или нажмите «🔙 Назад».")
            return
        if len(text) > TEXT_LIMIT:
            await message.answer(f"⚠️ Текст слишком длинный: {len(text)} из {TEXT_LIMIT} символов.")
            return
        await state.update_data(media_type="text", file_id="", caption=text)
        await _ask_url(message, state)
        return

    if message.photo:
        media_type, file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, file_id = "video", message.video.file_id
    elif message.audio:
        media_type, file_id = "audio", message.audio.file_id
    elif message.document:
        media_type, file_id = "document", message.document.file_id
    else:
        await message.answer("⚠️ Поддерживаются текст, фото, видео, аудио или документ.")
        return

    await state.update_data(media_type=media_type, file_id=file_id)
    await message.answer(f"✏️ Теперь отправьте подпись к медиа или «{_SKIP}», чтобы без подписи:")
    await state.set_state(AdminFlow.ask_ad_caption)


@router.message(AdminFlow.ask_ad_caption, IsBotAdminFilter(ADMINS))
async def handle_caption(message: types.Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer(f"✍️ Отправьте подпись текстом или «{_SKIP}».")
        return
    caption = "" if message.text.strip() == _SKIP else message.html_text
    if len(caption) > CAPTION_LIMIT:
        await message.answer(f"⚠️ Подпись слишком длинная: {len(caption)} из {CAPTION_LIMIT} символов.")
        return
    await state.update_data(caption=caption)
    await _ask_url(message, state)


@router.message(AdminFlow.ask_ad_url, IsBotAdminFilter(ADMINS))
async def handle_url(message: types.Message, state: FSMContext) -> None:
    url = (message.text or "").strip()
    if url == _SKIP:
        await state.update_data(url="", button_text="")
        await _show_preview(message, state)
        return
    if not url.startswith(("http://", "https://")):
        await message.answer(f"⚠️ Введите ссылку, начинающуюся с http:// или https://, или «{_SKIP}».")
        return
    await state.update_data(url=url)
    await message.answer("✏️ Введите текст для кнопки:")
    await state.set_state(AdminFlow.ask_ad_button_text)


@router.message(AdminFlow.ask_ad_button_text, IsBotAdminFilter(ADMINS))
async def handle_button_text(message: types.Message, state: FSMContext) -> None:
    button_text = (message.text or "").strip()
    if not button_text:
        await message.answer("⚠️ Текст кнопки не должен быть пустым.")
        return
    await state.update_data(button_text=button_text)
    await _show_preview(message, state)


async def _show_preview(message: types.Message, state: FSMContext) -> None:
    payload = payload_from_state(await state.get_data())
    try:
        validate_payload(payload)
    except InvalidBroadcast as e:
        await message.answer(f"❌ Рассылку нельзя отправить: {e}. Начните заново.", reply_markup=broadcasts_kb())
        await state.clear()
        return

    await message.answer("👁 Предпросмотр вашего поста:", reply_markup=reply_keyboard_for_admin())
    try:
        # Тот же код, что и в самой рассылке: что видит админ, то получат пользователи
        await deliver(message.bot, message.chat.id, payload)
    except Exception as e:
        await message.answer(
            f"❌ Telegram не принял сообщение: <code>{e}</code>\nПроверьте текст и ссылку и начните заново.",
            parse_mode="HTML",
            reply_markup=broadcasts_kb(),
        )
        await state.clear()
        return

    recipients = await db.count_broadcast_recipients()
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send_ad"),
                InlineKeyboardButton(text="⏳ Запланировать", callback_data="plan_send_ad"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_send_ad")],
        ]
    )
    await message.answer(
        f"Отправить эту рассылку? Получателей: <b>{recipients}</b>\n"
        "(пользователи, заблокировавшие бота, не учитываются)",
        parse_mode="HTML",
        reply_markup=confirm_keyboard,
    )
    await state.set_state(AdminFlow.are_you_sure)


# ==========================
# Запуск
# ==========================

@router.callback_query(F.data == "confirm_send_ad", IsBotAdminFilter(ADMINS))
async def confirm_send_ad(call: types.CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        return
    msg_any = cast(Any, call.message)
    payload: dict[str, Any] = payload_from_state(await state.get_data())
    try:
        validate_payload(payload)
    except InvalidBroadcast:
        await call.answer("⚠️ Черновик устарел. Создайте рассылку заново.", show_alert=True)
        await state.clear()
        return

    progress = await msg_any.edit_text("🚀 Рассылка поставлена в очередь...", parse_mode="HTML")
    payload["progress_chat_id"] = call.message.chat.id
    payload["progress_message_id"] = getattr(progress, "message_id", None) or call.message.message_id

    job = cast(
        dict[str, Any] | None,
        await db.create_broadcast_job(call.from_user.id, json.dumps(payload, ensure_ascii=False), "manual"),
    )
    await state.clear()
    if not job:
        await msg_any.edit_text("❌ Не удалось создать рассылку.")
        return

    job_id = int(job["id"])
    await audit_log_service.record_admin_action(
        actor_id=call.from_user.id,
        action="broadcast_job_created",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={"media_type": payload.get("media_type"), "source": "manual"},
    )
    await msg_any.edit_text(
        f"🚀 Рассылка <code>{job_id}</code> запущена.\n"
        "Прогресс будет обновляться в этом сообщении. Остановить: «⏳ Запланированные».",
        parse_mode="HTML",
    )
    broadcast_runner.wake_up()


@router.message(AdminFlow.ask_ad_schedule_at, IsBotAdminFilter(ADMINS))
async def schedule_broadcast_input(message: types.Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    raw = (message.text or "").strip()
    try:
        scheduled_for = datetime.strptime(raw, SCHEDULE_DT_FORMAT)
    except ValueError:
        await message.answer(
            "❌ Неверный формат времени.\nИспользуйте: <code>YYYY-MM-DD HH:MM</code>",
            parse_mode="HTML",
        )
        return
    if scheduled_for <= datetime.now():
        await message.answer("❌ Время должно быть в будущем.")
        return

    payload = payload_from_state(await state.get_data())
    try:
        validate_payload(payload)
    except InvalidBroadcast as e:
        await message.answer(f"❌ Рассылку нельзя запланировать: {e}.", reply_markup=broadcasts_kb())
        await state.clear()
        return

    created = cast(
        dict[str, Any] | None,
        await db.create_scheduled_broadcast(
            actor_id=user.id,
            scheduled_for=scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
            payload_json=json.dumps(payload, ensure_ascii=False),
        ),
    )
    await state.clear()
    if not created:
        await message.answer("❌ Не удалось создать задачу рассылки.", reply_markup=broadcasts_kb())
        return
    job_id = int(created.get("id") or 0)
    await audit_log_service.record_admin_action(
        actor_id=user.id,
        action="broadcast_scheduled_created",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={"scheduled_for": scheduled_for.strftime(SCHEDULE_DT_FORMAT)},
    )
    await message.answer(
        f"✅ Рассылка запланирована.\n🆔 <code>{job_id}</code>\n🕒 {scheduled_for.strftime(SCHEDULE_DT_FORMAT)}\n"
        "Итог придёт вам в личные сообщения.",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


# ==========================
# Списки
# ==========================

@router.message(F.text == "⏳ Запланированные", IsBotAdminFilter(ADMINS))
async def broadcast_active_menu(message: types.Message) -> None:
    jobs = cast(list[dict[str, Any]], await db.list_scheduled_broadcasts(limit=20) or [])
    if not jobs:
        await message.answer(
            "⏳ <b>Активные рассылки</b>\n\nНет ожидающих или идущих рассылок.",
            parse_mode="HTML",
            reply_markup=broadcasts_kb(),
        )
        return

    await message.answer(
        f"⏳ <b>Активные рассылки</b>: <b>{len(jobs)}</b>\n"
        "Прогресс хранится в базе: после перезапуска бота рассылка продолжится с места остановки.",
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )
    for job in jobs:
        job_id = int(job.get("id") or 0)
        status = str(job.get("status") or "")
        body = (
            f"🆔 <code>{job_id}</code> ({'ручная' if job.get('source') == 'manual' else 'плановая'})\n"
            f"🕒 {_fmt_dt(job.get('scheduled_for'))}\n"
            f"Статус: <b>{_STATUS_TEXT.get(status, status)}</b>"
        )
        if status == "running":
            done = sum(int(job.get(k) or 0) for k in ("success_count", "failed_count", "blocked_count"))
            body += f"\n📊 Обработано: {done}/{int(job.get('total_users') or 0)}"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🛑 Остановить" if status == "running" else "🛑 Отменить",
                    callback_data=f"cancel_scheduled_{job_id}",
                )
            ]]
        )
        await message.answer(body, parse_mode="HTML", reply_markup=markup)


@router.message(F.text == "📋 История", IsBotAdminFilter(ADMINS))
async def broadcast_history_menu(message: types.Message) -> None:
    rows = cast(list[dict[str, Any]], await db.get_broadcast_history(25) or [])
    if not rows:
        await message.answer(
            "📋 <b>История рассылок</b>\n\nПока нет завершённых рассылок.",
            parse_mode="HTML",
            reply_markup=broadcasts_kb(),
        )
        return

    lines: list[str] = []
    for row in rows:
        status = str(row.get("status") or "")
        lines.append(
            f"• <b>{_fmt_dt(row.get('happened_at'))}</b> | "
            f"{'Ручная' if row.get('source') == 'manual' else 'Плановая'}\n"
            f"  id: <code>{row.get('entity_id') or '-'}</code> | admin: <code>{row.get('actor_id') or '-'}</code>\n"
            f"  статус: <b>{_STATUS_TEXT.get(status, status)}</b>\n"
            f"  доставлено: <b>{int(row.get('success_count') or 0)}/{int(row.get('total_users') or 0)}</b>, "
            f"заблокировали: <b>{int(row.get('blocked_count') or 0)}</b>, "
            f"ошибок: <b>{int(row.get('failed_count') or 0)}</b>"
        )
    await message.answer(
        "📋 <b>История рассылок</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=broadcasts_kb(),
    )


# ==========================
# Блокировка бота пользователем
# ==========================

@member_router.my_chat_member()
async def on_private_bot_status(event: types.ChatMemberUpdated) -> None:
    user_id = event.from_user.id
    new_status = event.new_chat_member.status
    try:
        if new_status == "kicked":
            await db.mark_bot_blocked(user_id)
        elif new_status == "member":
            await db.clear_bot_blocked(user_id)
    except Exception as e:
        logger.warning(f"Не удалось обновить статус блокировки бота для {user_id}: {e}")
