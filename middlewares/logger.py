# middlewares/logger.py

import asyncio
import html
import re
import time
from collections import deque
from typing import Callable, Awaitable, Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, TelegramObject
from loguru import logger

from data.config import LOG_GROUP_ID
from services.activity import activity_service

# Состояния, в которых пользователь вводит платёжные данные — их текст не логируем вовсе
_SENSITIVE_STATE_PREFIXES = ("WithdrawalFlow:", "ReferralFlow:CARD", "ReferralFlow:PHONE")

# Любая последовательность из 8+ цифр (с пробелами/дефисами) — карта/телефон → маскируем
_DIGITS_RE = re.compile(r"(?:\d[\s-]?){8,}")

# Telegram ограничивает ~20 сообщений/мин в одну группу — держим запас
_MAX_LOGS_PER_MINUTE = 15


def _mask(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"****{digits[-4:]}"

    return _DIGITS_RE.sub(repl, text)


class ActionLoggerMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._sent_at: deque[float] = deque()
        # Держим ссылки на фоновые задачи, иначе сборщик мусора может их прервать
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _can_send(self) -> bool:
        now = time.monotonic()
        while self._sent_at and now - self._sent_at[0] > 60:
            self._sent_at.popleft()
        if len(self._sent_at) >= _MAX_LOGS_PER_MINUTE:
            return False
        self._sent_at.append(now)
        return True

    async def _send_log(self, bot: Any, text: str) -> None:
        # В фоне, чтобы отправка лога не задерживала ответ пользователю.
        # LOG_GROUP_ID необязателен: без него логи в группу не шлём (#51)
        if LOG_GROUP_ID is None or not self._can_send():
            return
        try:
            await bot.send_message(LOG_GROUP_ID, text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"Не удалось отправить лог в группу: {e}")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data["bot"]

        try:
            state: FSMContext | None = data.get("state")
            current_state = (await state.get_state()) if state else None
            sensitive = bool(current_state and current_state.startswith(_SENSITIVE_STATE_PREFIXES))

            if isinstance(event, Message) and event.text and event.from_user:
                user = event.from_user
                safe_text = "[скрыто: платёжные данные]" if sensitive else _mask(event.text)
                await activity_service.record(
                    user_id=user.id,
                    event="message_received",
                    chat_id=event.chat.id,
                    session_id=str(event.message_id),
                    payload={"text": safe_text[:500]},
                )
                self._spawn(self._send_log(
                    bot,
                    f"💬 <b>Сообщение</b>\n"
                    f"👤 <a href='tg://user?id={user.id}'>{html.escape(user.full_name)}</a>\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"📝 {html.escape(safe_text[:1000])}",
                ))

            elif isinstance(event, CallbackQuery):
                user = event.from_user
                chat_id = event.message.chat.id if event.message else None
                await activity_service.record(
                    user_id=user.id,
                    event="callback_query",
                    chat_id=chat_id,
                    session_id=event.id,
                    payload={"data": event.data},
                )
                self._spawn(self._send_log(
                    bot,
                    f"🔘 <b>Кнопка</b>\n"
                    f"👤 <a href='tg://user?id={user.id}'>{html.escape(user.full_name)}</a>\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"📌 {html.escape(event.data or '')}",
                ))
        except Exception as e:
            logger.debug(f"ActionLoggerMiddleware: {e}")

        return await handler(event, data)
