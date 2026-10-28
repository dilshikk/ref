"""
Исправленная обёртка над Database.

- execute() теперь ВСЕГДА делает commit: раньше запросы с fetchone/fetchall
  (в т.ч. INSERT/UPDATE ... RETURNING — отложенные рассылки) откатывались.
- Атомарные операции с заявками на выплату (защита от двойной обработки)
  и списание только выплаченной суммы вместо обнуления баланса.
- Проверка блокировки пользователя.
- Атомарное начисление за реферала (защита от двойного начисления).
- Баланс хранится в ДЕНЬГАХ (balance_sum / earned_sum), а не считается как
  «количество × текущая цена». Смена цены за реферала больше не пересчитывает
  уже заработанные деньги. current_count/history_count остаются как счётчики людей.
- Если приглашённый вышел из группы — начисление за него снимается (пункт 8).
- При одобрении выплаты из счётчика «добавил N участников» списывается норма,
  поэтому одни и те же добавленные не открывают вывод повторно (пункт 11).
- Награда за голос (vote_credit) начисляется один раз на вопрос (пункт 14).
"""
from __future__ import annotations

from typing import Any, cast

from loguru import logger
from sqlalchemy import text

from data.config import url_yaml
from utils.db.postgres import Database


def _current_price() -> int:
    try:
        return max(0, int(url_yaml.get("invited_credit", 0) or 0))
    except (TypeError, ValueError):
        return 0


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

    # ======================================================
    # Баланс в деньгах
    # ======================================================
    async def ensure_money_columns(self) -> None:
        """
        Миграция: добавляет balance_sum и earned_sum.
        Один раз (при первом запуске) заполняет их по текущей цене — это ровно те
        суммы, которые пользователи видят сейчас. Всё выполняется одной транзакцией:
        если что-то упадёт, колонки не появятся пустыми.
        """
        price = _current_price()  # int — безопасно подставлять в DO-блок (там нельзя параметры)
        await self.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'userinvitecount' AND column_name = 'balance_sum'
                ) THEN
                    ALTER TABLE UserInviteCount ADD COLUMN balance_sum BIGINT NOT NULL DEFAULT 0;
                    ALTER TABLE UserInviteCount ADD COLUMN earned_sum BIGINT NOT NULL DEFAULT 0;
                    UPDATE UserInviteCount
                    SET balance_sum = CAST(current_count AS BIGINT) * {price},
                        earned_sum  = CAST(history_count AS BIGINT) * {price};
                    RAISE NOTICE 'balance_sum/earned_sum backfilled with price {price}';
                END IF;
            END $$;
            """
        )
        logger.info("✅ Балансы хранятся в деньгах (balance_sum / earned_sum).")

    async def get_user_invited_count(self, user_id: int):
        return await self.execute(
            """
            SELECT current_count, history_count, balance_sum, earned_sum
            FROM UserInviteCount WHERE user_id = $1
            """,
            parameters=(user_id,),
            fetchone=True,
        )

    async def get_user_profile(self, telegram_id: int):
        return await self.execute(
            """
            SELECT u.*,
                   COALESCE(ic.current_count, 0) AS current_count,
                   COALESCE(ic.history_count, 0) AS history_count,
                   COALESCE(ic.balance_sum, 0)   AS balance_sum,
                   COALESCE(ic.earned_sum, 0)    AS earned_sum
            FROM Users u
            LEFT JOIN UserInviteCount ic ON ic.user_id = u.telegram_id
            WHERE u.telegram_id = $1
            """,
            parameters=(telegram_id,),
            fetchone=True,
        )

    async def get_top_referrals(self, limit: int = 10):
        return await self.execute(
            """
            SELECT u.full_name, u.username, ic.history_count, ic.earned_sum
            FROM UserInviteCount ic
            JOIN Users u ON u.telegram_id = ic.user_id
            ORDER BY ic.history_count DESC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def add_balance(self, user_id: int, amount: int, price: int | None = None) -> None:
        """Бонус от админа: amount приглашений по цене на момент начисления."""
        money = amount * (_current_price() if price is None else price)
        await self.execute(
            """
            INSERT INTO UserInviteCount (user_id, current_count, history_count, balance_sum, earned_sum)
            VALUES ($1, $2, $2, CAST($3 AS BIGINT), CAST($3 AS BIGINT))
            ON CONFLICT (user_id) DO UPDATE
                SET current_count = UserInviteCount.current_count + $2,
                    history_count = UserInviteCount.history_count + $2,
                    balance_sum   = UserInviteCount.balance_sum + CAST($3 AS BIGINT),
                    earned_sum    = UserInviteCount.earned_sum + CAST($3 AS BIGINT)
            """,
            parameters=(user_id, amount, money),
        )

    async def update_invite_current_count_set_0(self, user_id: int) -> None:
        """Обнуление баланса админом: и счётчик, и деньги. История сохраняется."""
        await self.execute(
            "UPDATE UserInviteCount SET current_count = 0, balance_sum = 0 WHERE user_id = $1",
            parameters=(user_id,),
        )

    # ======================================================
    # Выплаты
    # ======================================================
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

    async def approve_payout_if_pending(
        self,
        request_id: int,
        credits_to_deduct: int,
        group_id: int | None = None,
        members_to_deduct: int = 0,
    ):
        """
        В одной транзакции: pending → approved, списание ровно выплаченной суммы
        (деньги — из balance_sum, счётчик людей — на credits_to_deduct) и
        списание members_to_deduct из счётчика «добавил N участников» в группе
        group_id — чтобы одних и тех же добавленных нельзя было использовать
        для следующего вывода. Лишние добавленные сверх нормы сохраняются.
        Возвращает строку или None, если заявка уже обработана.
        """
        return await self.execute(
            """
            WITH upd AS (
                UPDATE PayoutRequests SET status = 'approved'
                WHERE id = $1 AND status = 'pending'
                RETURNING id, user_id, amount
            ), ded AS (
                UPDATE UserInviteCount uic
                SET current_count = GREATEST(0, uic.current_count - $2),
                    balance_sum   = GREATEST(0, uic.balance_sum - CAST(CEIL(upd.amount) AS BIGINT))
                FROM upd
                WHERE uic.user_id = upd.user_id
                RETURNING uic.user_id
            ), grp AS (
                UPDATE GroupMembers gm
                SET add_user_count = GREATEST(0, gm.add_user_count - $4)
                FROM upd
                WHERE gm.user_id = upd.user_id
                  AND gm.group_id = CAST($3 AS BIGINT)
                  AND $4 > 0
                RETURNING gm.user_id
            )
            SELECT id, user_id, amount FROM upd
            """,
            parameters=(request_id, credits_to_deduct, group_id, max(0, int(members_to_deduct))),
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

    # ======================================================
    # Рефералы
    # ======================================================
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

    async def credit_referral_once(self, referrer_id: int, invited_user_id: int, reward: int) -> bool:
        """
        Атомарно: записывает приглашённого (вместе с начисленной суммой — она нужна,
        чтобы снять ровно её, если человек выйдет из группы), +1 к счётчику и +reward
        к балансу реферера (reward — цена на МОМЕНТ начисления). Если этот человек уже
        засчитан (в т.ч. при двух одновременных событиях от Telegram или повторном
        вступлении после выхода), ничего не меняет и возвращает False.
        """
        row = await self.execute(
            """
            WITH ins AS (
                INSERT INTO UserInviteMember (user_id, inveted_user_id, reward_sum)
                VALUES ($1, $2, CAST($3 AS BIGINT))
                ON CONFLICT (inveted_user_id) DO NOTHING
                RETURNING user_id
            ), cnt AS (
                INSERT INTO UserInviteCount (user_id, current_count, history_count, balance_sum, earned_sum)
                SELECT user_id, 1, 1, CAST($3 AS BIGINT), CAST($3 AS BIGINT) FROM ins
                ON CONFLICT (user_id) DO UPDATE
                    SET current_count = UserInviteCount.current_count + 1,
                        history_count = UserInviteCount.history_count + 1,
                        balance_sum   = UserInviteCount.balance_sum + CAST($3 AS BIGINT),
                        earned_sum    = UserInviteCount.earned_sum + CAST($3 AS BIGINT)
                RETURNING user_id
            )
            SELECT user_id FROM ins
            """,
            parameters=(referrer_id, invited_user_id, max(0, int(reward))),
            fetchone=True,
        )
        return row is not None

    # ======================================================
    # Выход приглашённого из группы (пункт 8)
    # ======================================================
    async def ensure_referral_reversal_columns(self) -> None:
        """
        Миграция: в UserInviteMember запоминаем, сколько денег начислено за человека
        (reward_sum) и когда начисление снято (reversed_at). Старым записям reward_sum
        заполняется по текущей цене — так же, как балансы в ensure_money_columns.
        """
        price = _current_price()  # int — безопасно подставлять в DO-блок
        await self.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'userinvitemember' AND column_name = 'reward_sum'
                ) THEN
                    ALTER TABLE UserInviteMember ADD COLUMN reward_sum BIGINT NOT NULL DEFAULT 0;
                    UPDATE UserInviteMember SET reward_sum = {price};
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'userinvitemember' AND column_name = 'reversed_at'
                ) THEN
                    ALTER TABLE UserInviteMember ADD COLUMN reversed_at TIMESTAMP NULL;
                END IF;
            END $$;
            """
        )
        logger.info("✅ Снятие начисления при выходе из группы готово (reward_sum / reversed_at).")

    async def reverse_referral_on_leave(self, invited_user_id: int, hold_hours: int, group_id: int):
        """
        Атомарно снимает начисление за приглашённого, который вышел из группы:
        - только если начисление ещё не снято и человек вышел в течение hold_hours
          после вступления (hold_hours <= 0 — снимать всегда);
        - у пригласившего: -1 к счётчикам, -reward_sum к балансу и заработку,
          -1 к «добавил N участников» в этой группе. Ниже нуля ничего не уходит.
        Запись приглашённого остаётся (с reversed_at), поэтому при повторном
        вступлении деньги второй раз не начислятся.
        Возвращает {user_id, reward_sum} или None, если снимать нечего.
        """
        return await self.execute(
            """
            WITH rev AS (
                UPDATE UserInviteMember
                SET reversed_at = NOW()
                WHERE inveted_user_id = $1
                  AND reversed_at IS NULL
                  AND (
                      CAST($2 AS INT) <= 0
                      OR invited_at >= NOW() - make_interval(hours => CAST($2 AS INT))
                  )
                RETURNING user_id, reward_sum
            ), ded AS (
                UPDATE UserInviteCount uic
                SET current_count = GREATEST(0, uic.current_count - 1),
                    history_count = GREATEST(0, uic.history_count - 1),
                    balance_sum   = GREATEST(0, uic.balance_sum - rev.reward_sum),
                    earned_sum    = GREATEST(0, uic.earned_sum - rev.reward_sum)
                FROM rev
                WHERE uic.user_id = rev.user_id
                RETURNING uic.user_id
            ), grp AS (
                UPDATE GroupMembers gm
                SET add_user_count = GREATEST(0, gm.add_user_count - 1)
                FROM rev
                WHERE gm.user_id = rev.user_id AND gm.group_id = CAST($3 AS BIGINT)
                RETURNING gm.user_id
            )
            SELECT user_id, reward_sum FROM rev
            """,
            parameters=(invited_user_id, int(hold_hours), group_id),
            fetchone=True,
        )

    # ======================================================
    # Награда за голос (пункт 14)
    # ======================================================
    async def create_table_poll_vote_rewards(self) -> None:
        """Кому уже начислена награда за голос: одна награда на пользователя и вопрос."""
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_vote_rewards (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                question    TEXT NOT NULL,
                poll_id     VARCHAR(128) NOT NULL,
                reward_sum  BIGINT NOT NULL DEFAULT 0,
                created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, question)
            );
            """
        )

    async def reward_vote_once(self, poll_id: str, user_id: int, reward: int):
        """
        Атомарно начисляет reward за голос, если:
        - опрос создан этим ботом (есть в poll_instances);
        - пользователь ещё не получал награду за этот вопрос
          (новый опрос с тем же вопросом второй раз не оплачивается).
        Деньги идут в balance_sum / earned_sum, счётчики приглашённых не меняются.
        Возвращает {question, reward_sum} или None, если начислять нечего.
        """
        return await self.execute(
            """
            WITH ins AS (
                INSERT INTO poll_vote_rewards (user_id, question, poll_id, reward_sum)
                SELECT $2, pi.question, pi.poll_id, CAST($3 AS BIGINT)
                FROM poll_instances pi
                WHERE pi.poll_id = $1
                ON CONFLICT (user_id, question) DO NOTHING
                RETURNING user_id, question, reward_sum
            ), cnt AS (
                INSERT INTO UserInviteCount (user_id, current_count, history_count, balance_sum, earned_sum)
                SELECT user_id, 0, 0, reward_sum, reward_sum FROM ins WHERE reward_sum > 0
                ON CONFLICT (user_id) DO UPDATE
                    SET balance_sum = UserInviteCount.balance_sum + EXCLUDED.balance_sum,
                        earned_sum  = UserInviteCount.earned_sum + EXCLUDED.earned_sum
                RETURNING user_id
            )
            SELECT question, reward_sum FROM ins
            """,
            parameters=(poll_id, user_id, max(0, int(reward))),
            fetchone=True,
        )
