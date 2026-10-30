"""
Разбор ссылки «Проголосовать» на буст канала (t.me/boost/...).

Чистые функции без aiogram и базы — их проверяет tests/test_boosts.py.
- https://t.me/boost/restojobs   → "@restojobs" (публичный канал)
- https://t.me/boost?c=1234567890 → -1001234567890 (приватный канал)
Любая другая ссылка — None: за неё бот не может узнать, кто проголосовал.
"""
from __future__ import annotations

import re
from datetime import datetime

_BOOST_USERNAME_RE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/boost/([A-Za-z0-9_]{4,32})/?$", re.IGNORECASE)
_BOOST_PRIVATE_RE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/boost/?\?c=(\d+)$", re.IGNORECASE)

# Буст, убранный не раньше чем за час до срока, считаем истёкшим сам (кончился Premium)
EXPIRY_TOLERANCE_SECONDS = 3600


def boost_target(url: str | None) -> str | int | None:
    """Канал из ссылки на буст: "@username", ID приватного канала или None."""
    raw = (url or "").strip()
    match = _BOOST_USERNAME_RE.match(raw)
    if match:
        return f"@{match.group(1)}"
    match = _BOOST_PRIVATE_RE.match(raw)
    if match:
        return int(f"-100{match.group(1)}")
    return None


def is_boost_chat(
    target: str | int | None,
    chat_id: int,
    username: str | None,
    known_chat_id: int | None = None,
) -> bool:
    """
    Событие буста пришло из канала, указанного в ссылке «Проголосовать»?
    known_chat_id — ID канала, запомненный при сохранении ссылки: по нему канал
    узнаётся, даже если у него сменился юзернейм.
    """
    if target is None:
        return False
    if isinstance(target, int):
        return chat_id == target
    if known_chat_id is not None and chat_id == known_chat_id:
        return True
    return bool(username) and target[1:].lower() == str(username).lower()


def to_ts(value: datetime | None) -> int | None:
    """Дата Telegram → секунды Unix (или None)."""
    return int(value.timestamp()) if value is not None else None


def is_natural_expiry(removed_ts: int | None, expires_ts: int | None) -> bool:
    """Буст убран, потому что истёк сам (закончился Premium), а не отменён человеком?"""
    if removed_ts is None or expires_ts is None:
        return False
    return removed_ts >= expires_ts - EXPIRY_TOLERANCE_SECONDS
