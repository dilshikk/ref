# utils/db/referral_db.py
"""
Дополнения к SafeDatabase по итогам аудита.

- approve_payout_if_pending: баланс и норма «добавил N участников» проверяются
  в том же SQL, что и списание (строки блокируются FOR UPDATE). Раньше баланс
  проверялся в Python, а списание шло через GREATEST(0, …): выход реферала между
  ними уводил баланс в ноль.
- get_payout_approval_state: почему одобрение не прошло — для сообщения админу.
- referral_holds: вступления сверх антифрод-лимита больше не теряются, а ждут
  решения администратора (/holds).
- _database_url: строка подключения собирается через URL.create (#21).
- реквизиты выплат шифруются при записи и расшифровываются при чтении (#20).
- add_payout_request: «одна заявка в ожидании» и «одна заявка в сутки» проверяются
  в том же SQL, что и вставка, плюс уникальный индекс в БД (#47).
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.engine import URL

from utils import payment_details
from utils.db.safe_db import SafeDatabase


def _decrypt_row(row: Any) -> dict[str, Any] | None:
    """Строка заявки с расшифрованным card_number (обычный dict вместо RowMapping)."""
    if row is None:
        return None
    data = dict(row)
    if "card_number" in data:
        data["card_number"] = payment_details.decrypt(data["card_number"])
    return data


def _decrypt_rows(rows: Any) -> list[dict[str, Any]]:
    return [r for r in (_decrypt_row(row) for row in rows or []) if r is not None]


class ReferralDatabase(SafeDatabase):
    def _database_url(self) -> URL:
        """
        Раньше строка собиралась f-строкой, и пароль с «@», «/», «:» или «#»
        ломал подключение. URL.create экранирует каждый компонент сам (#21).
        """
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.user,
            password=self.password or None,
            host=self.host,
            port=self.port,
            database=self.db_name,
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

        Условия одобрения проверяются ЗДЕСЬ ЖЕ, в SQL, а не в Python перед вызовом:
        - баланс пользователя не меньше суммы заявки;
        - добавлено не меньше members_to_deduct участников (если норма > 0).
        Строки заявки и баланса блокируются (FOR UPDATE), поэтому выход реферала
        между проверкой и списанием больше не уводит баланс «в ноль» через GREATEST.

        Возвращает строку или None, если заявка уже обработана ИЛИ условия не выполнены
        (причину можно узнать через get_payout_approval_state).
        """
        return await self.execute(
            """
            WITH chk AS (
                SELECT pr.id, pr.user_id, pr.amount
                FROM PayoutRequests pr
                JOIN UserInviteCount uic ON uic.user_id = pr.user_id
                WHERE pr.id = $1
                  AND pr.status = 'pending'
                  AND uic.balance_sum >= CAST(CEIL(pr.amount) AS BIGINT)
                  AND (
                      $4 <= 0
                      OR COALESCE((
                          SELECT gm.add_user_count FROM GroupMembers gm
                          WHERE gm.user_id = pr.user_id AND gm.group_id = CAST($3 AS BIGINT)
                      ), 0) >= $4
                  )
                FOR UPDATE OF pr, uic
            ), upd AS (
                UPDATE PayoutRequests p SET status = 'approved'
                FROM chk
                WHERE p.id = chk.id
                RETURNING p.id, p.user_id, p.amount
            ), ded AS (
                UPDATE UserInviteCount uic
                SET current_count = GREATEST(0, uic.current_count - $2),
                    balance_sum   = uic.balance_sum - CAST(CEIL(upd.amount) AS BIGINT)
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

    async def get_payout_approval_state(self, request_id: int, group_id: int | None):
        """Статус заявки, текущий баланс и число добавленных — чтобы объяснить админу отказ."""
        return await self.execute(
            """
            SELECT pr.status,
                   pr.amount,
                   COALESCE(uic.balance_sum, 0) AS balance_sum,
                   COALESCE(gm.add_user_count, 0) AS add_user_count
            FROM PayoutRequests pr
            LEFT JOIN UserInviteCount uic ON uic.user_id = pr.user_id
            LEFT JOIN GroupMembers gm
                   ON gm.user_id = pr.user_id AND gm.group_id = CAST($2 AS BIGINT)
            WHERE pr.id = $1
            """,
            parameters=(request_id, group_id),
            fetchone=True,
        )

    # ======================================================
    # Удержание рефералов сверх антифрод-лимита
    # ======================================================
    async def create_table_referral_holds(self) -> None:
        """
        Вступления сверх антифрод-лимита раньше просто терялись. Теперь они
        записываются «на удержание» и ждут решения администратора.
        Один приглашённый попадает сюда не более одного раза.
        """
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_holds (
                id               SERIAL PRIMARY KEY,
                referrer_id      BIGINT NOT NULL,
                invited_user_id  BIGINT NOT NULL UNIQUE,
                invited_name     VARCHAR(255),
                group_id         BIGINT NOT NULL,
                status           VARCHAR(16) NOT NULL DEFAULT 'pending',
                created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                decided_at       TIMESTAMP NULL,
                decided_by       BIGINT NULL
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_referral_holds_referrer_status "
            "ON referral_holds (referrer_id, status);"
        )

    async def hold_referral(self, referrer_id: int, invited_user_id: int, invited_name: str, group_id: int) -> bool:
        """Записывает вступление «на удержание». False — этот человек уже был удержан раньше."""
        row = await self.execute(
            """
            INSERT INTO referral_holds (referrer_id, invited_user_id, invited_name, group_id)
            VALUES ($1, $2, $3, CAST($4 AS BIGINT))
            ON CONFLICT (invited_user_id) DO NOTHING
            RETURNING id
            """,
            parameters=(referrer_id, invited_user_id, invited_name[:255], group_id),
            fetchone=True,
        )
        return row is not None

    async def has_referral_hold(self, invited_user_id: int) -> bool:
        row = await self.execute(
            "SELECT 1 AS x FROM referral_holds WHERE invited_user_id = $1",
            parameters=(invited_user_id,),
            fetchone=True,
        )
        return row is not None

    async def cancel_referral_hold_on_leave(self, invited_user_id: int) -> None:
        """Удержанный вышел из группы до решения админа — начислять за него нечего."""
        await self.execute(
            """
            UPDATE referral_holds SET status = 'left', decided_at = NOW()
            WHERE invited_user_id = $1 AND status = 'pending'
            """,
            parameters=(invited_user_id,),
        )

    async def list_pending_referral_holds(self, limit: int = 20):
        """Пригласившие с удержанными вступлениями: сколько и с какого времени."""
        return await self.execute(
            """
            SELECT referrer_id, COUNT(*)::int AS held, MIN(created_at) AS first_at
            FROM referral_holds
            WHERE status = 'pending'
            GROUP BY referrer_id
            ORDER BY held DESC
            LIMIT $1
            """,
            parameters=(limit,),
            fetchall=True,
        )

    async def approve_referral_holds(self, referrer_id: int, admin_id: int, reward: int, group_id: int) -> dict[str, int]:
        """
        Атомарно начисляет все удержанные вступления пригласившего по текущей цене:
        запись в UserInviteMember (её же снимет выход из группы), +N к счётчикам,
        +N × reward к балансу и +N к «добавил N участников».
        Возвращает {claimed, credited}: credited может быть меньше, если человек
        уже был засчитан другим путём.
        """
        row = self._as_dict(
            await self.execute(
                """
                WITH claimed AS (
                    UPDATE referral_holds
                    SET status = 'approved', decided_at = NOW(), decided_by = $2
                    WHERE referrer_id = $1 AND status = 'pending'
                    RETURNING invited_user_id
                ), ins AS (
                    INSERT INTO UserInviteMember (user_id, inveted_user_id, reward_sum)
                    SELECT $1, invited_user_id, CAST($3 AS BIGINT) FROM claimed
                    ON CONFLICT (inveted_user_id) DO NOTHING
                    RETURNING inveted_user_id
                ), n AS (
                    SELECT COUNT(*)::int AS c FROM ins
                ), cnt AS (
                    INSERT INTO UserInviteCount (user_id, current_count, history_count, balance_sum, earned_sum)
                    SELECT $1, c, c, CAST(c AS BIGINT) * CAST($3 AS BIGINT), CAST(c AS BIGINT) * CAST($3 AS BIGINT)
                    FROM n WHERE c > 0
                    ON CONFLICT (user_id) DO UPDATE
                        SET current_count = UserInviteCount.current_count + EXCLUDED.current_count,
                            history_count = UserInviteCount.history_count + EXCLUDED.history_count,
                            balance_sum   = UserInviteCount.balance_sum + EXCLUDED.balance_sum,
                            earned_sum    = UserInviteCount.earned_sum + EXCLUDED.earned_sum
                    RETURNING user_id
                ), grp AS (
                    INSERT INTO GroupMembers (group_id, user_id, add_user_count)
                    SELECT CAST($4 AS BIGINT), $1, c FROM n WHERE c > 0
                    ON CONFLICT (group_id, user_id) DO UPDATE
                        SET add_user_count = GroupMembers.add_user_count + EXCLUDED.add_user_count
                    RETURNING user_id
                )
                SELECT (SELECT COUNT(*) FROM claimed)::int AS claimed, (SELECT c FROM n) AS credited
                """,
                parameters=(referrer_id, admin_id, max(0, int(reward)), group_id),
                fetchone=True,
            )
        )
        data = row or {}
        return {"claimed": int(data.get("claimed") or 0), "credited": int(data.get("credited") or 0)}

    async def reject_referral_holds(self, referrer_id: int, admin_id: int) -> int:
        row = self._as_dict(
            await self.execute(
                """
                WITH rej AS (
                    UPDATE referral_holds
                    SET status = 'rejected', decided_at = NOW(), decided_by = $2
                    WHERE referrer_id = $1 AND status = 'pending'
                    RETURNING id
                )
                SELECT COUNT(*) AS count FROM rej
                """,
                parameters=(referrer_id, admin_id),
                fetchone=True,
            )
        )
        return self._count_from_row(row)

    # ======================================================
    # Реквизиты выплат: шифрование (#20)
    # ======================================================
    async def ensure_payout_card_encryption(self, batch_size: int = 500) -> None:
        """
        Миграция при старте:
        - card_number VARCHAR(50) → TEXT: зашифрованное значение длиннее 50 символов;
        - если задан PAYOUT_ENCRYPTION_KEY, шифрует старые заявки пачками.
        """
        await self.execute("ALTER TABLE PayoutRequests ALTER COLUMN card_number TYPE TEXT")
        if not payment_details.encryption_enabled():
            return

        total = 0
        while True:
            rows = await self.execute(
                """
                SELECT id, card_number FROM PayoutRequests
                WHERE card_number IS NOT NULL AND card_number NOT LIKE 'enc:%'
                ORDER BY id
                LIMIT $1
                """,
                parameters=(batch_size,),
                fetchall=True,
            )
            if not rows:
                break
            for row in rows:
                await self.execute(
                    "UPDATE PayoutRequests SET card_number = $1 WHERE id = $2 AND card_number = $3",
                    parameters=(payment_details.encrypt(row["card_number"]), row["id"], row["card_number"]),
                )
            total += len(rows)
            if len(rows) < batch_size:
                break
        if total:
            logger.info(f"🔐 Зашифрованы реквизиты старых заявок: {total}")

    # ======================================================
    # Одна заявка в ожидании на пользователя (#47)
    # ======================================================
    async def ensure_unique_pending_payout(self) -> None:
        """
        Миграция: у пользователя может быть только одна заявка в статусе pending.
        Уже существующие дубли (если были) отклоняются, кроме самой ранней, иначе
        индекс не создастся. Отклонение ничего не списывает: деньги списываются
        только при одобрении, так что баланс пользователя не меняется.
        """
        rejected = await self.execute(
            """
            WITH dup AS (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at, id) AS rn
                    FROM PayoutRequests
                    WHERE status = 'pending'
                ) t
                WHERE rn > 1
            )
            UPDATE PayoutRequests p SET status = 'rejected'
            FROM dup
            WHERE p.id = dup.id
            RETURNING p.id, p.user_id
            """,
            fetchall=True,
        )
        if rejected:
            ids = ", ".join(f"#{r['id']} (user {r['user_id']})" for r in rejected)
            logger.warning(f"⚠️ Отклонены повторные заявки на вывод в ожидании: {ids}")
        await self.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payout_pending_user "
            "ON PayoutRequests (user_id) WHERE status = 'pending'"
        )
        logger.info("✅ Одна заявка на вывод в ожидании на пользователя (uq_payout_pending_user).")

    async def add_payout_request(
        self,
        user_id: int,
        full_name: str,
        username: str | None,
        card_number: str,
        amount: float,
    ) -> int | None:
        """
        Создаёт заявку и возвращает её ID или None, если у пользователя уже есть
        заявка в ожидании или заявка за последние 24 часа.
        Правило «раз в сутки» проверяется в том же запросе, что и вставка, а две
        одновременные вставки (второй экземпляр бота, двойное нажатие) упираются
        в уникальный индекс uq_payout_pending_user — ON CONFLICT DO NOTHING.
        """
        row = await self.execute(
            """
            INSERT INTO PayoutRequests (user_id, full_name, username, card_number, amount, status)
            SELECT $1, $2, $3, $4, $5, 'pending'
            WHERE NOT EXISTS (
                SELECT 1 FROM PayoutRequests
                WHERE user_id = $1
                  AND (status = 'pending' OR created_at >= NOW() - INTERVAL '24 hours')
            )
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            parameters=(user_id, full_name, username, payment_details.encrypt(card_number), amount),
            fetchone=True,
        )
        return int(row["id"]) if row else None

    async def get_pending_payouts(self):
        return _decrypt_rows(await super().get_pending_payouts())

    async def get_user_payouts(self, user_id: int):
        return _decrypt_rows(await super().get_user_payouts(user_id))

    async def get_user_payout_history(self, user_id: int):
        return _decrypt_rows(await super().get_user_payout_history(user_id))

    async def get_payout_request_by_id(self, request_id: int):
        return _decrypt_row(await super().get_payout_request_by_id(request_id))
