# utils/db/broadcast_db.py
"""
Запросы для рассылок с сохранением прогресса (#19).

Ручные и запланированные рассылки — задачи в scheduled_broadcasts (поле source).
После каждого получателя сохраняются last_user_pk (Users.id) и счётчики, поэтому
после перезапуска бота рассылка продолжается с места остановки.
Пользователи, заблокировавшие бота, помечаются bot_blocked_at и пропускаются.
"""
from __future__ import annotations

from typing import Any, cast

from loguru import logger

from utils.db.referral_db import ReferralDatabase


class BroadcastDatabase(ReferralDatabase):
    async def ensure_broadcast_progress_columns(self) -> None:
        await self.execute(
            """
            ALTER TABLE scheduled_broadcasts
                ADD COLUMN IF NOT EXISTS source        VARCHAR(16) NOT NULL DEFAULT 'scheduled',
                ADD COLUMN IF NOT EXISTS last_user_pk  BIGINT      NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS blocked_count INT         NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS max_user_pk   BIGINT      NOT NULL DEFAULT 0;
            """
        )
        await self.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS bot_blocked_at TIMESTAMP NULL;")
        logger.info("✅ Рассылки сохраняют прогресс (last_user_pk / bot_blocked_at).")

    async def create_broadcast_job(self, actor_id: int, payload_json: str, source: str = "manual"):
        """Задача «отправить сейчас»: планировщик возьмёт её сразу."""
        return await self.execute(
            """
            INSERT INTO scheduled_broadcasts (actor_id, scheduled_for, payload, source)
            VALUES ($1, NOW(), CAST($2 AS jsonb), $3)
            RETURNING *
            """,
            parameters=(actor_id, payload_json, source),
            fetchone=True,
        )

    async def fix_broadcast_audience(self, job_id: int) -> int:
        """
        При первом запуске запоминает максимальный Users.id: круг получателей
        фиксируется и после рестарта не меняется. Возвращает этот id.
        """
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                UPDATE scheduled_broadcasts
                SET max_user_pk = CASE
                    WHEN max_user_pk > 0 THEN max_user_pk
                    ELSE (SELECT COALESCE(MAX(id), 0) FROM Users)
                END
                WHERE id = $1
                RETURNING max_user_pk
                """,
                parameters=(job_id,),
                fetchone=True,
            ),
        )
        return int((row or {}).get("max_user_pk") or 0)

    async def count_broadcast_recipients(self, max_user_pk: int | None = None) -> int:
        """Сколько получат рассылку: все, кто не заблокировал бота (при max_user_pk — только до него)."""
        if max_user_pk is None:
            row = await self.execute(
                "SELECT COUNT(*) AS count FROM Users WHERE bot_blocked_at IS NULL",
                fetchone=True,
            )
        else:
            row = await self.execute(
                "SELECT COUNT(*) AS count FROM Users WHERE id <= $1 AND bot_blocked_at IS NULL",
                parameters=(max_user_pk,),
                fetchone=True,
            )
        return self._count_from_row(cast(dict[str, Any] | None, row))

    async def get_broadcast_recipients_after(self, last_user_pk: int, max_user_pk: int, limit: int):
        return await self.execute(
            """
            SELECT id, telegram_id
            FROM Users
            WHERE id > $1 AND id <= $2 AND bot_blocked_at IS NULL
            ORDER BY id
            LIMIT $3
            """,
            parameters=(last_user_pk, max_user_pk, limit),
            fetchall=True,
        )

    async def cancel_scheduled_broadcast(self, job_id: int):
        """Отменить можно и ожидающую, и уже идущую рассылку: цикл остановится на следующей пачке."""
        return await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'canceled', finished_at = NOW()
            WHERE id = $1 AND status IN ('pending', 'running')
            RETURNING *
            """,
            parameters=(job_id,),
            fetchone=True,
        )

    async def list_scheduled_broadcasts(self, limit: int = 20):
        """Только активные задачи (раньше в список попадали и давно завершённые)."""
        return await self.execute(
            """
            SELECT *
            FROM scheduled_broadcasts
            WHERE status IN ('pending', 'running')
            ORDER BY scheduled_for ASC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def set_broadcast_total(self, job_id: int, total_users: int) -> None:
        await self.execute(
            "UPDATE scheduled_broadcasts SET total_users = $2 WHERE id = $1",
            parameters=(job_id, total_users),
        )

    async def save_broadcast_progress(
        self, job_id: int, last_user_pk: int, success_count: int, failed_count: int, blocked_count: int
    ) -> None:
        await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET last_user_pk = $2, success_count = $3, failed_count = $4, blocked_count = $5
            WHERE id = $1 AND status = 'running'
            """,
            parameters=(job_id, last_user_pk, success_count, failed_count, blocked_count),
        )

    async def finish_broadcast(
        self, job_id: int, total_users: int, success_count: int, failed_count: int, blocked_count: int
    ) -> None:
        await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'done',
                finished_at = NOW(),
                total_users = $2,
                success_count = $3,
                failed_count = $4,
                blocked_count = $5,
                last_error = NULL
            WHERE id = $1 AND status = 'running'
            """,
            parameters=(job_id, total_users, success_count, failed_count, blocked_count),
        )

    async def mark_bot_blocked(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE Users SET bot_blocked_at = NOW() WHERE telegram_id = $1 AND bot_blocked_at IS NULL",
            parameters=(telegram_id,),
        )

    async def clear_bot_blocked(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE Users SET bot_blocked_at = NULL WHERE telegram_id = $1 AND bot_blocked_at IS NOT NULL",
            parameters=(telegram_id,),
        )

    async def get_broadcast_history(self, limit: int = 25):
        """
        История: старые ручные рассылки (до #19) — из audit_logs, все новые и
        запланированные — из scheduled_broadcasts.
        """
        return await self.execute(
            """
            SELECT *
            FROM (
                SELECT
                    created_at AS happened_at,
                    'manual'::text AS source,
                    actor_id,
                    entity_id,
                    'done'::text AS status,
                    COALESCE((payload->>'total_users')::int, 0) AS total_users,
                    COALESCE((payload->>'success')::int, 0) AS success_count,
                    COALESCE((payload->>'failed')::int, 0) AS failed_count,
                    0 AS blocked_count
                FROM audit_logs
                WHERE action = 'broadcast_finished'

                UNION ALL

                SELECT
                    COALESCE(finished_at, started_at, created_at) AS happened_at,
                    source::text,
                    actor_id,
                    id::text AS entity_id,
                    status::text,
                    COALESCE(total_users, 0),
                    COALESCE(success_count, 0),
                    COALESCE(failed_count, 0),
                    COALESCE(blocked_count, 0)
                FROM scheduled_broadcasts
                WHERE status IN ('done', 'failed', 'canceled')
            ) t
            ORDER BY happened_at DESC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )
