# utils/fsm_storage.py
"""
Хранилище состояний форм (вывод средств, настройки админки и т.д.) в PostgreSQL.

Раньше использовался MemoryStorage: при любом перезапуске бота (обновление, падение,
перезагрузка сервера) все незаконченные формы терялись, и пользователь, нажав кнопку
или отправив номер карты, получал непонятный ответ. Теперь состояние лежит в той же
базе, что и остальные данные, и переживает перезапуск. Новых зависимостей не нужно.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, cast

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from utils.db.safe_db import SafeDatabase


def _key(key: StorageKey) -> str:
    return ":".join(
        str(part)
        for part in (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id or 0,
            key.business_connection_id or "",
            key.destiny,
        )
    )


class PostgresStorage(BaseStorage):
    def __init__(self, db: SafeDatabase) -> None:
        self._db = db

    async def create_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_storage (
                key         TEXT PRIMARY KEY,
                state       TEXT NULL,
                data        JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )
        # Брошенные формы старше 7 дней не нужны — чистим при старте
        await self._db.execute("DELETE FROM fsm_storage WHERE updated_at < NOW() - INTERVAL '7 days'")

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._db.execute(
            """
            INSERT INTO fsm_storage (key, state) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state, updated_at = NOW()
            """,
            parameters=(_key(key), value),
        )

    async def get_state(self, key: StorageKey) -> str | None:
        row = cast(
            dict[str, Any] | None,
            await self._db.execute(
                "SELECT state FROM fsm_storage WHERE key = $1",
                parameters=(_key(key),),
                fetchone=True,
            ),
        )
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO fsm_storage (key, data) VALUES ($1, CAST($2 AS jsonb))
            ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """,
            parameters=(_key(key), json.dumps(dict(data), ensure_ascii=False, default=str)),
        )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = cast(
            dict[str, Any] | None,
            await self._db.execute(
                "SELECT data FROM fsm_storage WHERE key = $1",
                parameters=(_key(key),),
                fetchone=True,
            ),
        )
        if not row or row["data"] is None:
            return {}
        data = row["data"]
        # asyncpg через SQLAlchemy может вернуть JSONB строкой
        return dict(json.loads(data) if isinstance(data, str) else data)

    async def close(self) -> None:
        # Соединениями с базой управляет db (закрывается в shutdown)
        return None
