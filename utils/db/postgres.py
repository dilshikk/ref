from __future__ import annotations

import json
import re
from typing import Any, cast

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(
        self,
        host: str = "127.0.0.1",
        user: str = "postgres",
        password: str = "",
        db_name: str = "referral_bot",
        port: int = 5432,
    ):
        self.host = host
        self.user = user
        self.password = password
        self.db_name = db_name
        self.port = port
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _database_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db_name}"

    @staticmethod
    def _prepare_sql(sql: str, parameters: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
        # Keep old asyncpg-style API ($1, $2, ...) while using SQLAlchemy text binds (:p1, :p2, ...).
        binds = {f"p{i}": value for i, value in enumerate(parameters, start=1)}

        def repl(match: re.Match[str]) -> str:
            return f":p{match.group(1)}"

        prepared_sql = re.sub(r"\$(\d+)", repl, sql)
        return prepared_sql, binds

    @staticmethod
    def _as_dict(row: Any) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, row)

    @staticmethod
    def _count_from_row(row: dict[str, Any] | None) -> int:
        return int((row or {}).get("count") or 0)

    async def create_pool(self) -> None:
        if self._engine is not None:
            return

        self._engine = create_async_engine(
            self._database_url(),
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(bind=self._engine, expire_on_commit=False)

    async def close_pool(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    async def _get_session(self) -> AsyncSession:
        if self._sessionmaker is None:
            raise RuntimeError("Database pool is not initialized. Call create_pool() first.")
        return self._sessionmaker()

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | None = None,
        fetchone: bool = False,
        fetchall: bool = False,
    ):
        if parameters is None:
            parameters = ()

        prepared_sql, binds = self._prepare_sql(sql, parameters)
        logger.debug("Executing SQL:\n{}", prepared_sql)

        session = await self._get_session()
        try:
            result = await session.execute(text(prepared_sql), binds)
            if fetchone:
                return result.mappings().first()
            if fetchall:
                return result.mappings().all()
            await session.commit()
            return None
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def create_table_users(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS Users (
                id           SERIAL PRIMARY KEY,
                full_name    VARCHAR(255) NOT NULL,
                username     VARCHAR(255),
                telegram_id  BIGINT UNIQUE,
                is_blocked   BOOLEAN DEFAULT FALSE,
                invite_link  TEXT DEFAULT NULL,
                language     VARCHAR(5) DEFAULT 'ru',
                created_at   TIMESTAMP DEFAULT NOW()
            );
            """
        )

    async def create_table_channel(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS channel (
                id      SERIAL PRIMARY KEY,
                chat_id VARCHAR(100) UNIQUE,
                link    VARCHAR(255) NOT NULL
            );
            """
        )

    async def create_table_users_invite_count(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS UserInviteCount (
                id            SERIAL PRIMARY KEY,
                user_id       BIGINT UNIQUE,
                current_count INT DEFAULT 0,
                history_count INT DEFAULT 0
            );
            """
        )

    async def create_table_user_invite_members(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS UserInviteMember (
                id              SERIAL PRIMARY KEY,
                user_id         BIGINT,
                inveted_user_id BIGINT,
                invited_at      TIMESTAMP DEFAULT NOW()
            );
            """
        )

    async def create_table_group_add_member_count(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS GroupMembers (
                id             SERIAL PRIMARY KEY,
                group_id       BIGINT,
                user_id        BIGINT,
                add_user_count INT DEFAULT 1,
                UNIQUE(group_id, user_id)
            );
            """
        )

    async def create_table_payout_requests(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS PayoutRequests (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT,
                full_name   VARCHAR(255),
                username    VARCHAR(255),
                card_number VARCHAR(50),
                amount      NUMERIC(10, 2),
                status      VARCHAR(50) DEFAULT 'pending',
                created_at  TIMESTAMP DEFAULT NOW()
            );
            """
        )

    async def create_table_audit_logs(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id          SERIAL PRIMARY KEY,
                created_at  TIMESTAMP DEFAULT NOW(),
                actor_id    BIGINT,
                actor_type  VARCHAR(32) NOT NULL,
                action      VARCHAR(128) NOT NULL,
                entity_type VARCHAR(64),
                entity_id   VARCHAR(128),
                old_value   JSONB,
                new_value   JSONB,
                payload     JSONB,
                ip          VARCHAR(64),
                metadata    JSONB
            );
            """
        )
        await self.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC);")
        await self.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_id ON audit_logs (actor_id);")
        await self.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs (action);")

    async def create_table_user_activity(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS user_activity (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                event       VARCHAR(128) NOT NULL,
                chat_id     BIGINT,
                created_at  TIMESTAMP DEFAULT NOW(),
                session_id  VARCHAR(128),
                payload     JSONB
            );
            """
        )
        await self.execute("CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON user_activity (created_at DESC);")
        await self.execute("CREATE INDEX IF NOT EXISTS idx_user_activity_user_id ON user_activity (user_id);")
        await self.execute("CREATE INDEX IF NOT EXISTS idx_user_activity_event ON user_activity (event);")

    async def create_table_scheduled_broadcasts(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
                id             BIGSERIAL PRIMARY KEY,
                actor_id       BIGINT,
                scheduled_for  TIMESTAMP NOT NULL,
                status         VARCHAR(32) NOT NULL DEFAULT 'pending',
                payload        JSONB NOT NULL,
                created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
                started_at     TIMESTAMP,
                finished_at    TIMESTAMP,
                total_users    INT,
                success_count  INT,
                failed_count   INT,
                last_error     TEXT
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_broadcast_status_time "
            "ON scheduled_broadcasts (status, scheduled_for);"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_broadcast_created_at "
            "ON scheduled_broadcasts (created_at DESC);"
        )

    async def create_table_broadcast_send_logs(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_send_logs (
                id               BIGSERIAL PRIMARY KEY,
                created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                source           VARCHAR(16) NOT NULL,
                actor_id         BIGINT,
                scheduled_job_id BIGINT,
                entity_id        VARCHAR(128),
                status           VARCHAR(32) NOT NULL DEFAULT 'done',
                total_users      INT NOT NULL DEFAULT 0,
                success_count    INT NOT NULL DEFAULT 0,
                failed_count     INT NOT NULL DEFAULT 0,
                error_text       TEXT,
                payload          JSONB
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_broadcast_logs_created_at "
            "ON broadcast_send_logs (created_at DESC);"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_broadcast_logs_source_status "
            "ON broadcast_send_logs (source, status);"
        )

    async def create_table_poll_instances(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_instances (
                poll_id      VARCHAR(128) PRIMARY KEY,
                question     TEXT NOT NULL,
                options      JSONB NOT NULL,
                source       VARCHAR(32) NOT NULL DEFAULT 'private',
                created_by   BIGINT,
                created_at   TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_poll_instances_created_at "
            "ON poll_instances (created_at DESC);"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_poll_instances_question "
            "ON poll_instances (question);"
        )

    async def create_table_poll_vote_answers(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_vote_answers (
                id               BIGSERIAL PRIMARY KEY,
                poll_id          VARCHAR(128) NOT NULL,
                user_id          BIGINT NOT NULL,
                selected_option  INT NOT NULL,
                created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (poll_id, user_id)
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_poll_answers_poll_id "
            "ON poll_vote_answers (poll_id);"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_poll_answers_user_id "
            "ON poll_vote_answers (user_id);"
        )

    async def add_is_blocked_column(self) -> None:
        await self.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE;")

    async def add_invited_at_column(self) -> None:
        await self.execute("ALTER TABLE UserInviteMember ADD COLUMN IF NOT EXISTS invited_at TIMESTAMP DEFAULT NOW();")

    async def add_invite_link_column(self) -> None:
        await self.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS invite_link TEXT DEFAULT NULL;")

    async def add_users_created_at_column(self) -> None:
        await self.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();")

    async def add_language_column(self) -> None:
        await self.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'ru';")

    async def get_user_invite_link(self, telegram_id: int) -> str | None:
        row = self._as_dict(
            await self.execute(
                "SELECT invite_link FROM Users WHERE telegram_id = $1",
                parameters=(telegram_id,),
                fetchone=True,
            )
        )
        return cast(str | None, (row or {}).get("invite_link"))

    async def save_user_invite_link(self, telegram_id: int, link: str) -> None:
        await self.execute(
            "UPDATE Users SET invite_link = $1 WHERE telegram_id = $2",
            parameters=(link, telegram_id),
        )

    async def get_user_by_invite_link(self, invite_link: str) -> int | None:
        row = self._as_dict(
            await self.execute(
                "SELECT telegram_id FROM Users WHERE invite_link = $1",
                parameters=(invite_link,),
                fetchone=True,
            )
        )
        value = (row or {}).get("telegram_id")
        return int(value) if value is not None else None

    @staticmethod
    def format_args(sql: str, parameters: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
        conditions = " AND ".join([f"{key} = ${i + 1}" for i, key in enumerate(parameters)])
        sql += conditions
        return sql, tuple(parameters.values())

    async def add_or_update_group_member(self, group_id: int, user_id: int) -> None:
        await self.execute(
            """
            INSERT INTO GroupMembers(group_id, user_id, add_user_count)
            VALUES ($1, $2, 1)
            ON CONFLICT (group_id, user_id) DO UPDATE
                SET add_user_count = GroupMembers.add_user_count + 1
            """,
            parameters=(group_id, user_id),
        )

    async def get_user_add_count(self, group_id: int, user_id: int):
        return await self.execute(
            "SELECT add_user_count FROM GroupMembers WHERE group_id=$1 AND user_id=$2",
            parameters=(group_id, user_id),
            fetchone=True,
        )

    async def get_group_member(self, user_id: int):
        return await self.execute(
            "SELECT * FROM GroupMembers WHERE user_id = $1",
            parameters=(user_id,),
            fetchone=True,
        )

    async def reset_group_member_count(self, user_id: int) -> None:
        await self.execute(
            "UPDATE GroupMembers SET add_user_count = 0 WHERE user_id = $1",
            parameters=(user_id,),
        )

    async def add_user(self, full_name: str, username: str | None, telegram_id: int) -> None:
        await self.execute(
            """
            INSERT INTO Users(full_name, username, telegram_id, created_at)
            VALUES($1, $2, $3, NOW())
            """,
            parameters=(full_name, username, telegram_id),
        )

    async def select_user(self, **kwargs):
        sql = "SELECT * FROM Users WHERE "
        sql, parameters = self.format_args(sql, kwargs)
        return await self.execute(sql, parameters=parameters, fetchone=True)

    async def select_all_users(self):
        return await self.execute("SELECT * FROM Users", fetchall=True)

    async def count_users(self):
        return await self.execute("SELECT COUNT(*) AS count FROM Users", fetchone=True)

    async def get_users_count(self):
        return await self.count_users()

    async def delete_users(self) -> None:
        await self.execute("DELETE FROM Users")

    async def get_users_paginated(self, offset: int = 0, limit: int = 10):
        return await self.execute(
            "SELECT * FROM Users ORDER BY id LIMIT $1 OFFSET $2",
            parameters=(limit, offset),
            fetchall=True,
        )

    async def search_users(self, query: str):
        pattern = f"%{query}%"
        return await self.execute(
            """
            SELECT * FROM Users
            WHERE CAST(telegram_id AS TEXT) LIKE $1
               OR LOWER(COALESCE(username, '')) LIKE LOWER($2)
               OR LOWER(full_name) LIKE LOWER($3)
            LIMIT 20
            """,
            parameters=(pattern, pattern, pattern),
            fetchall=True,
        )

    async def get_user_profile(self, telegram_id: int):
        return await self.execute(
            """
            SELECT u.*,
                   COALESCE(ic.current_count, 0) AS current_count,
                   COALESCE(ic.history_count, 0) AS history_count
            FROM Users u
            LEFT JOIN UserInviteCount ic ON ic.user_id = u.telegram_id
            WHERE u.telegram_id = $1
            """,
            parameters=(telegram_id,),
            fetchone=True,
        )

    async def block_user(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE Users SET is_blocked = TRUE WHERE telegram_id = $1",
            parameters=(telegram_id,),
        )

    async def unblock_user(self, telegram_id: int) -> None:
        await self.execute(
            "UPDATE Users SET is_blocked = FALSE WHERE telegram_id = $1",
            parameters=(telegram_id,),
        )

    async def update_user_name(self, telegram_id: int, new_name: str) -> None:
        await self.execute(
            "UPDATE Users SET full_name=$1 WHERE telegram_id=$2",
            parameters=(new_name, telegram_id),
        )

    async def set_user_language(self, telegram_id: int, lang: str) -> None:
        await self.execute(
            "UPDATE Users SET language = $1 WHERE telegram_id = $2",
            parameters=(lang, telegram_id),
        )

    async def get_user_language(self, telegram_id: int) -> str:
        row = self._as_dict(
            await self.execute(
                "SELECT language FROM Users WHERE telegram_id = $1",
                parameters=(telegram_id,),
                fetchone=True,
            )
        )
        return cast(str, (row or {}).get("language") or "ru")

    async def add_channel(self, chat_id: int, link: str) -> None:
        await self.execute(
            """
            INSERT INTO channel(chat_id, link)
            VALUES($1, $2)
            ON CONFLICT (chat_id) DO NOTHING
            """,
            parameters=(chat_id, link),
        )

    async def delete_channel(self, chat_id: int) -> None:
        await self.execute("DELETE FROM channel WHERE chat_id=$1", parameters=(chat_id,))

    async def get_all_channels(self):
        return await self.execute("SELECT * FROM channel", fetchall=True)

    async def add_user_invite_count(self, user_id: int, current_count: int, history_count: int) -> None:
        await self.execute(
            """
            INSERT INTO UserInviteCount(user_id, current_count, history_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
                SET current_count = UserInviteCount.current_count + 1,
                    history_count = UserInviteCount.history_count + 1
            """,
            parameters=(user_id, current_count, history_count),
        )

    async def get_user_invited_count(self, user_id: int):
        return await self.execute(
            "SELECT current_count, history_count FROM UserInviteCount WHERE user_id=$1",
            parameters=(user_id,),
            fetchone=True,
        )

    async def update_invite_current_history_count_plus_1(self, user_id: int) -> None:
        await self.execute(
            """
            UPDATE UserInviteCount
            SET current_count = current_count + 1, history_count = history_count + 1
            WHERE user_id = $1
            """,
            parameters=(user_id,),
        )

    async def update_invite_current_count_set_0(self, user_id: int) -> None:
        await self.execute(
            "UPDATE UserInviteCount SET current_count=0 WHERE user_id=$1",
            parameters=(user_id,),
        )

    async def add_balance(self, user_id: int, amount: int) -> None:
        await self.execute(
            """
            INSERT INTO UserInviteCount(user_id, current_count, history_count)
            VALUES ($1, $2, $2)
            ON CONFLICT (user_id) DO UPDATE
                SET current_count = UserInviteCount.current_count + $2,
                    history_count = UserInviteCount.history_count + $2
            """,
            parameters=(user_id, amount),
        )

    async def deduct_balance(self, user_id: int, amount_in_credits: int) -> None:
        await self.execute(
            "UPDATE UserInviteCount SET current_count = GREATEST(0, current_count - $1) WHERE user_id = $2",
            parameters=(amount_in_credits, user_id),
        )

    async def add_user_invite_member(self, user_id: int, invited_user_id: int) -> None:
        await self.execute(
            "INSERT INTO UserInviteMember(user_id, inveted_user_id) VALUES($1, $2)",
            parameters=(user_id, invited_user_id),
        )

    async def select_invited_members(self, **kwargs):
        sql = "SELECT * FROM UserInviteMember WHERE "
        sql, parameters = self.format_args(sql, kwargs)
        return await self.execute(sql, parameters=parameters, fetchall=True)

    async def get_referrals_last_hour(self, user_id: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(*) AS count FROM UserInviteMember
                WHERE user_id = $1
                  AND invited_at >= NOW() - INTERVAL '1 hour'
                """,
                parameters=(user_id,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def add_payout_request(
        self,
        user_id: int,
        full_name: str,
        username: str | None,
        card_number: str,
        amount: float,
    ) -> None:
        await self.execute(
            """
            INSERT INTO PayoutRequests (user_id, full_name, username, card_number, amount, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            """,
            parameters=(user_id, full_name, username, card_number, amount),
        )

    async def get_pending_payouts(self):
        return await self.execute(
            """
            SELECT id, user_id, full_name, username, card_number, created_at
            FROM PayoutRequests
            WHERE status = 'pending'
            ORDER BY created_at DESC
            """,
            fetchall=True,
        )

    async def get_pending_payouts_by_user(self, user_id: int):
        return await self.execute(
            "SELECT amount FROM PayoutRequests WHERE user_id=$1 AND status='pending'",
            parameters=(user_id,),
            fetchall=True,
        )

    async def get_user_payouts(self, user_id: int):
        return await self.execute(
            """
            SELECT id, amount, card_number, status, created_at
            FROM PayoutRequests WHERE user_id=$1
            ORDER BY created_at DESC LIMIT 20
            """,
            parameters=(user_id,),
            fetchall=True,
        )

    async def get_user_payout_history(self, user_id: int):
        return await self.execute(
            """
            SELECT * FROM PayoutRequests
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 10
            """,
            parameters=(user_id,),
            fetchall=True,
        )

    async def update_payout_status(self, request_id: int, new_status: str) -> None:
        await self.execute(
            "UPDATE PayoutRequests SET status=$1 WHERE id=$2",
            parameters=(new_status, request_id),
        )

    async def get_payout_request_by_id(self, request_id: int):
        return await self.execute(
            "SELECT * FROM PayoutRequests WHERE id = $1 LIMIT 1",
            parameters=(request_id,),
            fetchone=True,
        )

    async def get_pending_or_today_payout(self, user_id: int):
        return await self.execute(
            """
            SELECT id FROM PayoutRequests
            WHERE user_id = $1
              AND (status = 'pending' OR created_at >= NOW() - INTERVAL '24 hours')
            LIMIT 1
            """,
            parameters=(user_id,),
            fetchone=True,
        )

    async def get_top_referrals(self, limit: int = 10):
        return await self.execute(
            """
            SELECT u.full_name, u.username, ic.history_count
            FROM UserInviteCount ic
            JOIN Users u ON u.telegram_id = ic.user_id
            ORDER BY ic.history_count DESC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def insert_audit_log(
        self,
        actor_id: int | None,
        actor_type: str,
        action: str,
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        old_value: dict | list | str | int | float | bool | None = None,
        new_value: dict | list | str | int | float | bool | None = None,
        payload: dict | list | str | int | float | bool | None = None,
        ip: str | None = None,
        metadata: dict | list | str | int | float | bool | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO audit_logs (
                actor_id, actor_type, action, entity_type, entity_id,
                old_value, new_value, payload, ip, metadata
            ) VALUES (
                $1, $2, $3, $4, $5,
                CAST($6 AS jsonb), CAST($7 AS jsonb), CAST($8 AS jsonb), $9, CAST($10 AS jsonb)
            )
            """,
            parameters=(
                actor_id,
                actor_type,
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                ip,
                json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            ),
        )

    async def insert_user_activity(
        self,
        user_id: int,
        event: str,
        chat_id: int | None = None,
        session_id: str | None = None,
        payload: dict | list | str | int | float | bool | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO user_activity (user_id, event, chat_id, session_id, payload)
            VALUES ($1, $2, $3, $4, CAST($5 AS jsonb))
            """,
            parameters=(
                user_id,
                event,
                chat_id,
                session_id,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )

    async def count_active_users_since(self, days: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(DISTINCT user_id) AS count
                FROM user_activity
                WHERE created_at >= NOW() - make_interval(days => $1)
                """,
                parameters=(days,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def count_new_users_since(self, days: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(*) AS count
                FROM Users
                WHERE created_at >= NOW() - make_interval(days => $1)
                """,
                parameters=(days,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def count_returning_users_since(self, days: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                WITH active AS (
                    SELECT DISTINCT user_id
                    FROM user_activity
                    WHERE created_at >= NOW() - make_interval(days => $1)
                ),
                new_users AS (
                    SELECT telegram_id AS user_id
                    FROM Users
                    WHERE created_at >= NOW() - make_interval(days => $1)
                )
                SELECT COUNT(*) AS count
                FROM active a
                LEFT JOIN new_users n ON n.user_id = a.user_id
                WHERE n.user_id IS NULL
                """,
                parameters=(days,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def count_activity_events_since(self, days: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(*) AS count
                FROM user_activity
                WHERE created_at >= NOW() - make_interval(days => $1)
                """,
                parameters=(days,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def count_audit_logs_since(self, days: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(*) AS count
                FROM audit_logs
                WHERE created_at >= NOW() - make_interval(days => $1)
                """,
                parameters=(days,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def get_last_user_activity_at(self, user_id: int):
        return await self.execute(
            """
            SELECT created_at
            FROM user_activity
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            parameters=(user_id,),
            fetchone=True,
        )

    async def get_user_activity_history(self, user_id: int, limit: int = 10):
        return await self.execute(
            """
            SELECT *
            FROM user_activity
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            parameters=(user_id, limit),
            fetchall=True,
        )

    async def create_scheduled_broadcast(self, actor_id: int, scheduled_for: str, payload_json: str):
        return await self.execute(
            """
            INSERT INTO scheduled_broadcasts (actor_id, scheduled_for, payload)
            VALUES ($1, CAST($2 AS timestamp), CAST($3 AS jsonb))
            RETURNING *
            """,
            parameters=(actor_id, scheduled_for, payload_json),
            fetchone=True,
        )

    async def list_scheduled_broadcasts(self, limit: int = 20):
        return await self.execute(
            """
            SELECT *
            FROM scheduled_broadcasts
            ORDER BY scheduled_for ASC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def cancel_scheduled_broadcast(self, job_id: int):
        return await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'canceled', finished_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            parameters=(job_id,),
            fetchone=True,
        )

    async def requeue_running_scheduled_broadcasts(self) -> None:
        await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'pending', started_at = NULL
            WHERE status = 'running'
            """
        )

    async def claim_due_scheduled_broadcasts(self, limit: int = 5):
        return await self.execute(
            """
            WITH due AS (
                SELECT id
                FROM scheduled_broadcasts
                WHERE status = 'pending'
                  AND scheduled_for <= NOW()
                ORDER BY scheduled_for ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE scheduled_broadcasts sb
            SET status = 'running', started_at = NOW(), last_error = NULL
            FROM due
            WHERE sb.id = due.id
            RETURNING sb.*
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def mark_scheduled_broadcast_done(self, job_id: int, total_users: int, success_count: int, failed_count: int) -> None:
        await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'done',
                finished_at = NOW(),
                total_users = $2,
                success_count = $3,
                failed_count = $4,
                last_error = NULL
            WHERE id = $1
            """,
            parameters=(job_id, total_users, success_count, failed_count),
        )

    async def mark_scheduled_broadcast_failed(self, job_id: int, error_text: str) -> None:
        await self.execute(
            """
            UPDATE scheduled_broadcasts
            SET status = 'failed',
                finished_at = NOW(),
                last_error = $2
            WHERE id = $1
            """,
            parameters=(job_id, error_text),
        )

    async def insert_broadcast_send_log(
        self,
        *,
        source: str,
        actor_id: int | None,
        status: str,
        total_users: int,
        success_count: int,
        failed_count: int,
        scheduled_job_id: int | None = None,
        entity_id: str | None = None,
        error_text: str | None = None,
        payload: dict | list | str | int | float | bool | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO broadcast_send_logs (
                source,
                actor_id,
                scheduled_job_id,
                entity_id,
                status,
                total_users,
                success_count,
                failed_count,
                error_text,
                payload
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, CAST($10 AS jsonb)
            )
            """,
            parameters=(
                source,
                actor_id,
                scheduled_job_id,
                entity_id,
                status,
                total_users,
                success_count,
                failed_count,
                error_text,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )

    async def get_broadcast_metrics(self, days: int | None = None) -> dict[str, int]:
        if days is None:
            row = self._as_dict(
                await self.execute(
                    """
                    SELECT
                        COUNT(*) AS total_runs,
                        COALESCE(SUM(total_users), 0) AS total_users,
                        COALESCE(SUM(success_count), 0) AS success_count,
                        COALESCE(SUM(failed_count), 0) AS failed_count,
                        COALESCE(SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END), 0) AS done_runs,
                        COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_runs,
                        COALESCE(SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END), 0) AS canceled_runs,
                        COALESCE(SUM(CASE WHEN source = 'manual' THEN 1 ELSE 0 END), 0) AS manual_runs,
                        COALESCE(SUM(CASE WHEN source = 'scheduled' THEN 1 ELSE 0 END), 0) AS scheduled_runs
                    FROM broadcast_send_logs
                    """,
                    fetchone=True,
                )
            )
        else:
            row = self._as_dict(
                await self.execute(
                    """
                    SELECT
                        COUNT(*) AS total_runs,
                        COALESCE(SUM(total_users), 0) AS total_users,
                        COALESCE(SUM(success_count), 0) AS success_count,
                        COALESCE(SUM(failed_count), 0) AS failed_count,
                        COALESCE(SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END), 0) AS done_runs,
                        COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_runs,
                        COALESCE(SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END), 0) AS canceled_runs,
                        COALESCE(SUM(CASE WHEN source = 'manual' THEN 1 ELSE 0 END), 0) AS manual_runs,
                        COALESCE(SUM(CASE WHEN source = 'scheduled' THEN 1 ELSE 0 END), 0) AS scheduled_runs
                    FROM broadcast_send_logs
                    WHERE created_at >= NOW() - make_interval(days => $1)
                    """,
                    parameters=(days,),
                    fetchone=True,
                )
            )

        data = row or {}
        return {
            "total_runs": int(data.get("total_runs") or 0),
            "total_users": int(data.get("total_users") or 0),
            "success_count": int(data.get("success_count") or 0),
            "failed_count": int(data.get("failed_count") or 0),
            "done_runs": int(data.get("done_runs") or 0),
            "failed_runs": int(data.get("failed_runs") or 0),
            "canceled_runs": int(data.get("canceled_runs") or 0),
            "manual_runs": int(data.get("manual_runs") or 0),
            "scheduled_runs": int(data.get("scheduled_runs") or 0),
        }

    async def insert_poll_instance(
        self,
        *,
        poll_id: str,
        question: str,
        options: list[str],
        source: str = "private",
        created_by: int | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO poll_instances (poll_id, question, options, source, created_by)
            VALUES ($1, $2, CAST($3 AS jsonb), $4, $5)
            ON CONFLICT (poll_id) DO UPDATE
            SET question = EXCLUDED.question,
                options = EXCLUDED.options,
                source = EXCLUDED.source,
                created_by = EXCLUDED.created_by
            """,
            parameters=(
                poll_id,
                question,
                json.dumps(options, ensure_ascii=False),
                source,
                created_by,
            ),
        )

    async def upsert_poll_vote_answer(self, *, poll_id: str, user_id: int, selected_option: int) -> None:
        await self.execute(
            """
            INSERT INTO poll_vote_answers (poll_id, user_id, selected_option)
            VALUES ($1, $2, $3)
            ON CONFLICT (poll_id, user_id) DO UPDATE
            SET selected_option = EXCLUDED.selected_option,
                updated_at = NOW()
            """,
            parameters=(poll_id, user_id, selected_option),
        )

    async def get_poll_option_stats(self, question: str):
        return await self.execute(
            """
            SELECT pva.selected_option AS option_index, COUNT(*)::int AS votes
            FROM poll_vote_answers pva
            JOIN poll_instances pi ON pi.poll_id = pva.poll_id
            WHERE pi.question = $1
            GROUP BY pva.selected_option
            ORDER BY pva.selected_option ASC
            """,
            parameters=(question,),
            fetchall=True,
        )

    async def count_poll_voters(self, question: str) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(DISTINCT pva.user_id) AS count
                FROM poll_vote_answers pva
                JOIN poll_instances pi ON pi.poll_id = pva.poll_id
                WHERE pi.question = $1
                """,
                parameters=(question,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    async def count_poll_instances(self, question: str) -> int:
        row = self._as_dict(
            await self.execute(
                """
                SELECT COUNT(*) AS count
                FROM poll_instances
                WHERE question = $1
                """,
                parameters=(question,),
                fetchone=True,
            )
        )
        return self._count_from_row(row)
