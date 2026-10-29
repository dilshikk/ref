# utils/broadcast_send.py
"""
Отправка одного сообщения рассылки (#19).

Модуль не зависит от loader/конфига бота, поэтому тестируется отдельно.

- Поддерживаются текст (без медиа), фото, видео, аудио и документ.
  Неизвестный тип — ошибка черновика, а не «успешная» отправка.
- TelegramRetryAfter: ждём, сколько просит Telegram, и повторяем тому же получателю.
- TelegramForbiddenError и «chat not found»: получатель недоступен (заблокировал бота
  или удалил аккаунт) — помечается, в следующие рассылки не попадает.
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Awaitable, Callable

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MEDIA_TYPES = ("text", "photo", "video", "audio", "document")
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
MAX_ATTEMPTS = 3
# Не ждём дольше 5 минут подряд: иначе одна ошибка могла бы «повесить» рассылку
MAX_RETRY_AFTER_SEC = 300

# Ошибки 400, которые относятся к получателю, а не к самому сообщению
_UNREACHABLE_MARKERS = ("chat not found", "user not found", "peer_id_invalid", "user is deactivated")

Sleep = Callable[[float], Awaitable[Any]]


class SendResult(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"  # заблокировал бота / недоступен
    FAILED = "failed"


class InvalidBroadcast(ValueError):
    """Черновик рассылки нельзя отправить (нет текста, файла, неизвестный тип и т.п.)."""


def payload_from_state(data: dict[str, Any]) -> dict[str, str]:
    """Поля рассылки из FSM-черновика в том же формате, что пишет планировщик."""
    return {
        "media_type": str(data.get("media_type") or ""),
        "file_id": str(data.get("file_id") or ""),
        "caption": str(data.get("caption") or ""),
        "url": str(data.get("url") or ""),
        "button_text": str(data.get("button_text") or ""),
    }


def validate_payload(payload: dict[str, Any]) -> None:
    media_type = str(payload.get("media_type") or "")
    caption = str(payload.get("caption") or "")
    if media_type not in MEDIA_TYPES:
        raise InvalidBroadcast(f"неизвестный тип рассылки: {media_type or 'пусто'}")
    if media_type == "text":
        if not caption.strip():
            raise InvalidBroadcast("пустой текст рассылки")
        if len(caption) > TEXT_LIMIT:
            raise InvalidBroadcast(f"текст длиннее {TEXT_LIMIT} символов")
        return
    if not str(payload.get("file_id") or ""):
        raise InvalidBroadcast("нет файла для рассылки")
    if len(caption) > CAPTION_LIMIT:
        raise InvalidBroadcast(f"подпись к медиа длиннее {CAPTION_LIMIT} символов")


def make_keyboard(payload: dict[str, Any]) -> InlineKeyboardMarkup | None:
    url = str(payload.get("url") or "").strip()
    button_text = str(payload.get("button_text") or "").strip()
    if not url or not button_text:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]])


async def deliver(bot: Any, chat_id: int, payload: dict[str, Any]) -> None:
    """Одна попытка отправки. Ошибки Telegram пробрасываются наверх."""
    media_type = str(payload.get("media_type") or "")
    file_id = str(payload.get("file_id") or "")
    caption = str(payload.get("caption") or "") or None
    keyboard = make_keyboard(payload)

    if media_type == "text":
        await bot.send_message(chat_id=chat_id, text=caption or "", reply_markup=keyboard)
    elif media_type == "photo":
        await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "video":
        await bot.send_video(chat_id=chat_id, video=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "audio":
        await bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption, reply_markup=keyboard)
    elif media_type == "document":
        await bot.send_document(chat_id=chat_id, document=file_id, caption=caption, reply_markup=keyboard)
    else:
        raise InvalidBroadcast(f"неизвестный тип рассылки: {media_type or 'пусто'}")


async def send_one(
    bot: Any,
    chat_id: int,
    payload: dict[str, Any],
    sleep: Sleep = asyncio.sleep,
) -> tuple[SendResult, str | None]:
    """Отправляет сообщение одному получателю с повторами. Возвращает (результат, текст ошибки)."""
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await deliver(bot, chat_id, payload)
            return SendResult.OK, None
        except TelegramRetryAfter as e:
            last_error = str(e)
            await sleep(min(max(int(e.retry_after), 1), MAX_RETRY_AFTER_SEC))
        except TelegramForbiddenError as e:
            return SendResult.BLOCKED, str(e)
        except TelegramBadRequest as e:
            text = str(e).lower()
            if any(marker in text for marker in _UNREACHABLE_MARKERS):
                return SendResult.BLOCKED, str(e)
            # Ошибка в самом сообщении: повтор не поможет
            return SendResult.FAILED, str(e)
        except (TelegramNetworkError, TelegramServerError) as e:
            last_error = str(e)
            await sleep(2 * attempt)
        except InvalidBroadcast:
            raise
        except Exception as e:
            return SendResult.FAILED, str(e)
    return SendResult.FAILED, last_error
