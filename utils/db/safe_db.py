"""
Исправленная обёртка над Database.

- execute() теперь ВСЕГДА делает commit: раньше запросы с fetchone/fetchall
  (в т.ч. INSERT/UPDATE ... RETURNING — отложенные рассылки) откатывались.
- Атомарные операции с заявками на выплату (защита от двойной обработки)
  и списание только выплаченной суммы вместо обнуления баланса.
- Проверка блокировки пользователя.
- Атомарное начисление за реферала (защита от двойного начисления).
"""
from __future__ import annotations

from typing import Any, cast

from loguru import logger
from sqlalchemy import text

from utils.db.postgres import Database


class SafeDatabase(Database):
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
            rows: Any = None
            if fetchone:
                rows = result.mappings().first()
            elif fetchall:
                rows = result.mappings().all()
            await session.commit()
            return rows
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def reject_payout_if_pending(self, request_id: int):
        """pending → rejected. Возвращает строку или None, если заявка уже обработана."""
        return await self.execute(
            """
            UPDATE PayoutRequests SET status = 'rejected'
            WHERE id = $1 AND status = 'pending'
            RETURNING id, user_id, amount
            """,
            parameters=(request_id,),
            fetchone=True,
        )

    async def approve_payout_if_pending(self, request_id: int, credits_to_deduct: int):
        """
        В одной транзакции: pending → approved и списание ровно выплаченной
        суммы (в рефералах). Возвращает строку или None, если заявка уже обработана.
        """
        return await self.execute(
            """
            WITH upd AS (
                UPDATE PayoutRequests SET status = 'approved'
                WHERE id = $1 AND status = 'pending'
                RETURNING id, user_id, amount
            ), ded AS (
                UPDATE UserInviteCount uic
                SET current_count = GREATEST(0, uic.current_count - $2)
                FROM upd
                WHERE uic.user_id = upd.user_id
                RETURNING uic.user_id
            )
            SELECT id, user_id, amount FROM upd
            """,
            parameters=(request_id, credits_to_deduct),
            fetchone=True,
        )

    async def is_user_blocked(self, telegram_id: int) -> bool:
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                "SELECT is_blocked FROM Users WHERE telegram_id = $1",
                parameters=(telegram_id,),
                fetchone=True,
            ),
        )
        return bool((row or {}).get("is_blocked"))

    async def ensure_unique_invited_members(self) -> None:
        """
        Миграция: один приглашённый может быть засчитан только один раз.
        Сначала удаляем старые повторы (оставляем самую раннюю запись),
        иначе уникальный индекс не создастся. Балансы не трогаем.
        """
        await self.execute(
            """
            DELETE FROM UserInviteMember a
            USING UserInviteMember b
            WHERE a.inveted_user_id = b.inveted_user_id
              AND a.id > b.id
            """
        )
        await self.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_userinvitemember_invited
            ON UserInviteMember (inveted_user_id)
            """
        )

    async def credit_referral_once(self, referrer_id: int, invited_user_id: int) -> bool:
        """
        Атомарно: записывает приглашённого и +1 к счётчику реферера.
        Если этот человек уже засчитан (в т.ч. при двух одновременных событиях
        от Telegram), ничего не меняет и возвращает False.
        """
        row = await self.execute(
            """
            WITH ins AS (
                INSERT INTO UserInviteMember (user_id, inveted_user_id)
                VALUES ($1, $2)
                ON CONFLICT (inveted_user_id) DO NOTHING
                RETURNING user_id
            ), cnt AS (
                INSERT INTO UserInviteCount (user_id, current_count, history_count)
                SELECT user_id, 1, 1 FROM ins
                ON CONFLICT (user_id) DO UPDATE
                    SET current_count = UserInviteCount.current_count + 1,
                        history_count = UserInviteCount.history_count + 1
                RETURNING user_id
            )
            SELECT user_id FROM ins
            """,
            parameters=(referrer_id, invited_user_id),
            fetchone=True,
        )
        return row is not None
