# utils/profile_sync.py
"""
Имя и username пользователя раньше записывались только при первом /start и
больше не менялись: сменил username в Telegram — админ в профиле и в поиске
видел старый. Telegram присылает актуальные имя и username в каждом сообщении
и нажатии кнопки, поэтому сверяем их и обновляем запись в Users.

Здесь только чистая логика (без aiogram и базы), чтобы её можно было проверить
тестами. Сам middleware — middlewares/profile_sync.py.
"""
from __future__ import annotations

# Колонки full_name и username в Users — VARCHAR(255)
MAX_LEN = 255
# Кэш «что уже записано в базу», чтобы не писать на каждое сообщение
MAX_CACHE_SIZE = 50_000

ProfileKey = tuple[str, str | None]


def normalize(full_name: str | None, username: str | None) -> ProfileKey:
    """Имя и username в том виде, в каком они лягут в базу."""
    name = (full_name or "").strip()[:MAX_LEN]
    nick = (username or "").strip().lstrip("@")[:MAX_LEN] or None
    return name, nick


class ProfileCache:
    """Последние записанные в базу имя и username по каждому пользователю."""

    def __init__(self, max_size: int = MAX_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._data: dict[int, ProfileKey] = {}

    def changed(self, user_id: int, key: ProfileKey) -> bool:
        """True — нужно сверить с базой (новый пользователь после перезапуска или данные изменились)."""
        return self._data.get(user_id) != key

    def remember(self, user_id: int, key: ProfileKey) -> None:
        if len(self._data) >= self._max_size and user_id not in self._data:
            self._data.clear()
        self._data[user_id] = key
