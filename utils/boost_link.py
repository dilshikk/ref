"""
Разбор ссылки «Проголосовать» на буст канала (t.me/boost/...).

Чистые функции без aiogram и базы — их проверяет tests/test_boosts.py.
- https://t.me/boost/restojobs   → "@restojobs" (публичный канал)
- https://t.me/boost?c=1234567890 → -1001234567890 (приватный канал)
Любая другая ссылка — None: за неё бот не может узнать, кто проголосовал.
"""
from __future__ import annotations

import re

_BOOST_USERNAME_RE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/boost/([A-Za-z0-9_]{4,32})/?$", re.IGNORECASE)
_BOOST_PRIVATE_RE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/boost/?\?c=(\d+)$", re.IGNORECASE)


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


def is_boost_chat(target: str | int | None, chat_id: int, username: str | None) -> bool:
    """Событие буста пришло из канала, указанного в ссылке «Проголосовать»?"""
    if target is None:
        return False
    if isinstance(target, int):
        return chat_id == target
    return bool(username) and target[1:].lower() == str(username).lower()
