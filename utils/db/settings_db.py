"""
Настройки бота в таблице bot_settings (#113): ключ → значение JSON.

Раньше настройки жили в url.yml, а группа и денежные админы — в .env. Файл нужно
было монтировать в Docker и выдавать на него права, а изменения из админки
терялись при неправильной настройке. Теперь всё хранится в базе.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from loguru import logger
from sqlalchemy import text

from utils.db.antifraud_db import AntifraudDatabase
from utils.settings_migration import MigrationPlan


def _decode(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


class SettingsDatabase(AntifraudDatabase):
    async def create_table_bot_settings(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key         VARCHAR(64) PRIMARY KEY,
                value       JSONB NOT NULL,
                updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_by  BIGINT
            );
            """
        )

    async def get_all_settings(self) -> dict[str, Any]:
        # value::text — одинаковый разбор независимо от того, как драйвер отдаёт JSONB
        rows = await self.execute("SELECT key, value::text AS value FROM bot_settings", fetchall=True) or []
        return {str(row["key"]): _decode(row["value"]) for row in rows}

    async def set_setting(self, key: str, value: Any, actor_id: int | None) -> None:
        await self.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at, updated_by)
            VALUES ($1, CAST($2 AS jsonb), NOW(), $3)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
            """,
            parameters=(key, json.dumps(value, ensure_ascii=False), actor_id),
        )

    async def run_settings_migration(
        self, build: Callable[[dict[str, Any]], MigrationPlan]
    ) -> MigrationPlan:
        """
        Перенос старых настроек одной транзакцией: либо переносится всё
        (настройки, каналы, запись в журнале), либо ничего.
        Таблица блокируется, чтобы два запущенных бота не перенесли настройки дважды.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                await session.execute(text("LOCK TABLE bot_settings IN SHARE ROW EXCLUSIVE MODE"))
                rows = (
                    await session.execute(text("SELECT key, value::text AS value FROM bot_settings"))
                ).mappings().all()
                existing = {str(row["key"]): _decode(row["value"]) for row in rows}
                plan = build(existing)
                if plan.is_empty:
                    return plan

                for key, value in plan.values.items():
                    await session.execute(
                        text(
                            "INSERT INTO bot_settings (key, value) VALUES (:key, CAST(:value AS jsonb)) "
                            "ON CONFLICT (key) DO NOTHING"
                        ),
                        {"key": key, "value": json.dumps(value, ensure_ascii=False)},
                    )
                for chat_id, link in plan.channels:
                    await session.execute(
                        text("INSERT INTO channel (chat_id, link) VALUES (:chat_id, :link) ON CONFLICT (chat_id) DO NOTHING"),
                        {"chat_id": chat_id, "link": link},
                    )
                await session.execute(
                    text(
                        "INSERT INTO audit_logs (actor_type, action, entity_type, entity_id, new_value, payload) "
                        "VALUES ('system', 'settings_migrated', 'setting', 'bot_settings', "
                        "CAST(:new_value AS jsonb), CAST(:payload AS jsonb))"
                    ),
                    {
                        "new_value": json.dumps(plan.values, ensure_ascii=False),
                        "payload": json.dumps(
                            {
                                "sources": plan.sources,
                                "channels": [chat_id for chat_id, _ in plan.channels],
                                "skipped": plan.skipped,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
            logger.info(f"✅ Настройки перенесены в базу: {sorted(plan.values)}; каналов: {len(plan.channels)}")
            return plan
        finally:
            await session.close()
