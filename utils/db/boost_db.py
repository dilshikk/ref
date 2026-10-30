"""
Награда за голос (буст) канала.

Одна запись на человека (UNIQUE user_id): платим один раз за всё время,
даже если человек убрал буст и поставил снова. При отмене буста в срок
запись остаётся с reversed_at — повторно деньги не начислятся.

У Premium-пользователя может быть несколько бустов. В записи хранится один из них;
если его убрали, а другие остались — запись переносится на оставшийся (move_boost_reward).
expires_ts — когда буст истекает сам: такое истечение начисление не снимает.
"""
from __future__ import annotations

from typing import Any, cast

from utils.db.settings_db import SettingsDatabase


class BoostDatabase(SettingsDatabase):
    async def create_table_boost_rewards(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS boost_rewards (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL UNIQUE,
                chat_id      BIGINT NOT NULL,
                boost_id     VARCHAR(128) NOT NULL,
                reward_sum   BIGINT NOT NULL DEFAULT 0,
                rewarded_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                reversed_at  TIMESTAMP NULL,
                expires_ts   BIGINT NULL
            );
            """
        )
        # Таблица могла быть создана предыдущей версией без expires_ts
        await self.execute("ALTER TABLE boost_rewards ADD COLUMN IF NOT EXISTS expires_ts BIGINT NULL;")
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_boost_rewards_boost ON boost_rewards (chat_id, boost_id);"
        )

    async def reward_boost_once(
        self, user_id: int, chat_id: int, boost_id: str, reward: int, expires_ts: int | None = None
    ):
        """
        Атомарно: запись о буст-награде и +reward к балансу и заработку.
        Если человек уже получал награду за буст — ничего не меняет, возвращает None.
        """
        return await self.execute(
            """
            WITH ins AS (
                INSERT INTO boost_rewards (user_id, chat_id, boost_id, reward_sum, expires_ts)
                VALUES ($1, CAST($2 AS BIGINT), $3, CAST($4 AS BIGINT), CAST($5 AS BIGINT))
                ON CONFLICT (user_id) DO NOTHING
                RETURNING user_id, reward_sum
            ), cnt AS (
                INSERT INTO UserInviteCount (user_id, current_count, history_count, balance_sum, earned_sum)
                SELECT user_id, 0, 0, reward_sum, reward_sum FROM ins WHERE reward_sum > 0
                ON CONFLICT (user_id) DO UPDATE
                    SET balance_sum = UserInviteCount.balance_sum + EXCLUDED.balance_sum,
                        earned_sum  = UserInviteCount.earned_sum + EXCLUDED.earned_sum
                RETURNING user_id
            )
            SELECT user_id, reward_sum FROM ins
            """,
            parameters=(user_id, chat_id, str(boost_id), max(0, int(reward)), expires_ts),
            fetchone=True,
        )

    async def get_active_boost_reward(self, chat_id: int, boost_id: str):
        """Неснятая награда за этот буст: {user_id, reward_sum, expires_ts} или None."""
        return await self.execute(
            """
            SELECT user_id, reward_sum, expires_ts
            FROM boost_rewards
            WHERE chat_id = CAST($1 AS BIGINT) AND boost_id = $2 AND reversed_at IS NULL
            """,
            parameters=(chat_id, str(boost_id)),
            fetchone=True,
        )

    async def move_boost_reward(
        self, chat_id: int, old_boost_id: str, new_boost_id: str, expires_ts: int | None
    ):
        """Человек убрал один буст, но другие остались: запись переходит на оставшийся."""
        return await self.execute(
            """
            UPDATE boost_rewards
            SET boost_id = $3, expires_ts = CAST($4 AS BIGINT)
            WHERE chat_id = CAST($1 AS BIGINT) AND boost_id = $2 AND reversed_at IS NULL
            RETURNING user_id
            """,
            parameters=(chat_id, str(old_boost_id), str(new_boost_id), expires_ts),
            fetchone=True,
        )

    async def reverse_boost_reward(self, chat_id: int, boost_id: str, hold_hours: int):
        """
        Буст убран: снимаем начисление, если с оплаты прошло меньше hold_hours
        (hold_hours <= 0 — снимать всегда). Если деньги уже выведены, остаётся долг
        (баланс уходит в минус), как и при выходе приглашённого из группы (#46).
        Возвращает {user_id, reward_sum} или None, если снимать нечего.
        """
        return await self.execute(
            """
            WITH rev AS (
                UPDATE boost_rewards
                SET reversed_at = NOW()
                WHERE chat_id = CAST($1 AS BIGINT)
                  AND boost_id = $2
                  AND reversed_at IS NULL
                  AND (
                      CAST($3 AS INT) <= 0
                      OR rewarded_at >= NOW() - make_interval(hours => CAST($3 AS INT))
                  )
                RETURNING user_id, reward_sum
            ), ded AS (
                UPDATE UserInviteCount uic
                SET balance_sum = uic.balance_sum - rev.reward_sum,
                    earned_sum  = GREATEST(0, uic.earned_sum - rev.reward_sum)
                FROM rev
                WHERE uic.user_id = rev.user_id
                RETURNING uic.user_id
            )
            SELECT user_id, reward_sum FROM rev
            """,
            parameters=(chat_id, str(boost_id), int(hold_hours)),
            fetchone=True,
        )

    async def boost_reward_stats(self) -> dict[str, int]:
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE reversed_at IS NULL) AS active
                FROM boost_rewards
                """,
                fetchone=True,
            ),
        )
        data = row or {}
        return {"total": int(data.get("total") or 0), "active": int(data.get("active") or 0)}

    async def list_recent_boost_rewards(self, limit: int = 10):
        return await self.execute(
            """
            SELECT b.user_id, u.full_name, u.username, b.reward_sum, b.rewarded_at, b.reversed_at
            FROM boost_rewards b
            LEFT JOIN Users u ON u.telegram_id = b.user_id
            ORDER BY b.rewarded_at DESC, b.id DESC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )
