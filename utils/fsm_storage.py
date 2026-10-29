# utils/fsm_storage.py
"""
Хранилище состояний форм (вывод средств, настройки админки и т.д.) в PostgreSQL.

Раньше использовался MemoryStorage: при любом перезапуске бота (обновление, падение,
перезагрузка сервера) все незаконченные формы терялись, и пользователь, нажав кнопку
или отправив номер карты, получал непонятный ответ. Теперь состояние лежит в той же
базе, что и остальные данные, и переживает перезапуск. Новых зависимостей не нужно.

Реквизиты в формах (#44):
- номер карты/телефон и имя держателя шифруются тем же ключом, что и заявки
  (PAYOUT_ENCRYPTION_KEY), — в базе и бэкапах их больше нет открытым текстом;
- из брошенных форм реквизиты стираются через час, сами формы — через 7 дней.
  Очистка идёт не только при старте, но и по ходу работы (не чаще раза в 10 минут);
- state.clear() удаляет строку целиком, а не оставляет пустую.
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Mapping, cast

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from utils import payment_details

if TYPE_CHECKING:
    from utils.db.safe_db import SafeDatabase

# Сколько живут реквизиты в незаконченной форме вывода
SENSITIVE_TTL_MINUTES = 60
# Сколько живёт брошенная форма целиком
FORM_TTL_DAYS = 7
# Как часто запускать очистку по ходу работы
CLEANUP_INTERVAL_SECONDS = 600


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
        self._last_cleanup = 0.0

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
        await self.cleanup()

    async def cleanup(self) -> None:
        """Стирает реквизиты из старых форм, удаляет брошенные и пустые строки."""
        self._last_cleanup = time.monotonic()
        await self._db.execute(
            "DELETE FROM fsm_storage WHERE updated_at < NOW() - make_interval(days => CAST($1 AS INT))",
            parameters=(FORM_TTL_DAYS,),
        )
        # Брошенная на шаге ввода карты форма больше не хранит карту неделю
        await self._db.execute(
            """
            UPDATE fsm_storage
            SET data = data - 'payment_info' - 'card_holder'
            WHERE updated_at < NOW() - make_interval(mins => CAST($1 AS INT))
              AND (data ->> 'payment_info' IS NOT NULL OR data ->> 'card_holder' IS NOT NULL)
            """,
            parameters=(SENSITIVE_TTL_MINUTES,),
        )
        # Пустые строки от старого clear(), который не удалял запись
        await self._db.execute("DELETE FROM fsm_storage WHERE state IS NULL AND data = '{}'::jsonb")

    async def _maybe_cleanup(self) -> None:
        if time.monotonic() - self._last_cleanup >= CLEANUP_INTERVAL_SECONDS:
            await self.cleanup()

    async def _delete_if_empty(self, key: str) -> None:
        # clear() = set_state(None) + set_data({}): после второго вызова строка удаляется
        await self._db.execute(
            "DELETE FROM fsm_storage WHERE key = $1 AND state IS NULL AND data = '{}'::jsonb",
            parameters=(key,),
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        db_key = _key(key)
        await self._db.execute(
            """
            INSERT INTO fsm_storage (key, state) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state, updated_at = NOW()
            """,
            parameters=(db_key, value),
        )
        if value is None:
            await self._delete_if_empty(db_key)

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
        db_key = _key(key)
        # Реквизиты шифруются до записи в базу (#44)
        sealed = payment_details.seal_form(data)
        await self._db.execute(
            """
            INSERT INTO fsm_storage (key, data) VALUES ($1, CAST($2 AS jsonb))
            ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """,
            parameters=(db_key, json.dumps(sealed, ensure_ascii=False, default=str)),
        )
        if not sealed:
            await self._delete_if_empty(db_key)
        await self._maybe_cleanup()

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
        raw = dict(json.loads(data) if isinstance(data, str) else data)
        return payment_details.open_form(raw)

    async def close(self) -> None:
        # Соединениями с базой управляет db (закрывается в shutdown)
        return None
