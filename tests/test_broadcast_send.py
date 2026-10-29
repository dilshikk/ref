"""Тесты отправки рассылки (#19). Запуск: python -m pytest tests"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("aiogram")

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

# Модуль грузится по пути: импорт пакета utils тянет настройки бота (BOT_TOKEN и т.д.)
_PATH = Path(__file__).resolve().parent.parent / "utils" / "broadcast_send.py"
_spec = importlib.util.spec_from_file_location("broadcast_send", _PATH)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

_METHOD = SendMessage(chat_id=1, text="x")


class FakeBot:
    """Записывает вызовы; errors — что бросить на очередных попытках."""

    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    def _call(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.errors:
            raise self.errors.pop(0)

    async def send_message(self, **kw):
        self._call("send_message", kw)

    async def send_photo(self, **kw):
        self._call("send_photo", kw)

    async def send_video(self, **kw):
        self._call("send_video", kw)

    async def send_audio(self, **kw):
        self._call("send_audio", kw)

    async def send_document(self, **kw):
        self._call("send_document", kw)


def _run(coro):
    return asyncio.run(coro)


async def _no_sleep(_seconds):
    return None


TEXT = {"media_type": "text", "caption": "Привет", "url": "", "button_text": ""}
PHOTO = {"media_type": "photo", "file_id": "f1", "caption": "", "url": "https://t.me/x", "button_text": "Открыть"}


def test_text_broadcast_is_sent_as_message():
    # Раньше текстовая рассылка ничего не отправляла, но считалась успешной
    bot = FakeBot()
    result, _ = _run(bs.send_one(bot, 10, TEXT, sleep=_no_sleep))
    assert result is bs.SendResult.OK
    assert bot.calls[0][0] == "send_message"
    assert bot.calls[0][1]["text"] == "Привет"
    assert bot.calls[0][1]["reply_markup"] is None


def test_media_with_button():
    bot = FakeBot()
    _run(bs.send_one(bot, 10, PHOTO, sleep=_no_sleep))
    name, kw = bot.calls[0]
    assert name == "send_photo" and kw["photo"] == "f1"
    assert kw["caption"] is None
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/x"


def test_retry_after_waits_and_retries():
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    bot = FakeBot([TelegramRetryAfter(_METHOD, "flood", retry_after=7)])
    result, _ = _run(bs.send_one(bot, 10, TEXT, sleep=sleep))
    assert result is bs.SendResult.OK
    assert slept == [7]
    assert len(bot.calls) == 2


def test_retry_after_gives_up_after_max_attempts():
    errors = [TelegramRetryAfter(_METHOD, "flood", retry_after=1) for _ in range(bs.MAX_ATTEMPTS)]
    result, _ = _run(bs.send_one(FakeBot(errors), 10, TEXT, sleep=_no_sleep))
    assert result is bs.SendResult.FAILED


def test_forbidden_means_blocked():
    bot = FakeBot([TelegramForbiddenError(_METHOD, "bot was blocked by the user")])
    result, _ = _run(bs.send_one(bot, 10, TEXT, sleep=_no_sleep))
    assert result is bs.SendResult.BLOCKED
    assert len(bot.calls) == 1


def test_chat_not_found_means_blocked():
    bot = FakeBot([TelegramBadRequest(_METHOD, "Bad Request: chat not found")])
    result, _ = _run(bs.send_one(bot, 10, TEXT, sleep=_no_sleep))
    assert result is bs.SendResult.BLOCKED


def test_other_bad_request_is_failure_without_retry():
    bot = FakeBot([TelegramBadRequest(_METHOD, "Bad Request: can't parse entities")])
    result, _ = _run(bs.send_one(bot, 10, TEXT, sleep=_no_sleep))
    assert result is bs.SendResult.FAILED
    assert len(bot.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"media_type": "", "caption": "x"},
        {"media_type": "sticker", "file_id": "f"},
        {"media_type": "text", "caption": "   "},
        {"media_type": "photo", "file_id": ""},
        {"media_type": "photo", "file_id": "f", "caption": "x" * (bs.CAPTION_LIMIT + 1)},
        {"media_type": "text", "caption": "x" * (bs.TEXT_LIMIT + 1)},
    ],
)
def test_invalid_payloads_are_rejected(payload):
    with pytest.raises(bs.InvalidBroadcast):
        bs.validate_payload(payload)


def test_valid_payloads():
    bs.validate_payload(TEXT)
    bs.validate_payload(PHOTO)


def test_unknown_type_is_not_counted_as_success():
    with pytest.raises(bs.InvalidBroadcast):
        _run(bs.send_one(FakeBot(), 10, {"media_type": "sticker"}, sleep=_no_sleep))
