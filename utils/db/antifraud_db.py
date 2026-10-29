# utils/db/antifraud_db.py
"""
Запросы антифрода (#18, #46).

- get_referrals_last_day: суточный лимит приглашений в дополнение к часовому.
- withdraw_captcha_state: неверные ответы капчи и блокировка вывода. Хранится в БД,
  а не в FSM: /start делает state.clear(), и блокировку можно было бы сбросить.
- pending_group_adds: добавленные в группу ждут проверки «живости» и только потом
  увеличивают счётчик «добавил N участников» (GroupMembers.add_user_count).
- group_member_activity: кто писал в группу (сигнал для проверки).
- reverse_referral_on_leave: при выходе непроверенного участника счётчик
  «добавил N» не уменьшается — он и не увеличивался. Баланс может уйти в минус
  (долг), если деньги за ушедшего уже выведены (#46).
- get_unverified_referral_sum: деньги «на проверке», которые ещё нельзя вывести (#46).
"""
from __future__ import annotations

from typing import Any, cast

from utils.db.broadcast_db import BroadcastDatabase


class AntifraudDatabase(BroadcastDatabase):
    # ======================================================
    # Лимиты приглашений
    # ======================================================
    async def get_referrals_last_day(self, referrer_id: int) -> int:
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                SELECT COUNT(*) AS count FROM UserInviteMember
                WHERE user_id = $1 AND invited_at >= NOW() - INTERVAL '24 hours'
                """,
                parameters=(referrer_id,),
                fetchone=True,
            ),
        )
        return self._count_from_row(row)

    # ======================================================
    # Деньги «на проверке» (#46)
    # ======================================================
    async def get_unverified_referral_sum(
        self,
        user_id: int,
        hold_hours: int,
        group_id: int,
        check_quality: bool,
    ) -> int:
        """
        Сумма начислений за приглашённых, которые ещё нельзя вывести:
        - не прошло hold_hours с вступления — выход приглашённого ещё снимет начисление;
        - приглашённый не прошёл проверку живости (pending_group_adds без qualified_at),
          если проверка включена. Приглашённые до #18 (записи в pending_group_adds нет)
          считаются проверенными, как и в reverse_referral_on_leave.
        Раньше всё начисленное выводилось сразу, а норма «добавил N» проверяла только
        N человек — 200 непроверенных начислений проходили при 5 проверенных.
        """
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                SELECT COALESCE(SUM(m.reward_sum), 0)::bigint AS total
                FROM UserInviteMember m
                WHERE m.user_id = $1
                  AND m.reversed_at IS NULL
                  AND (
                      (CAST($2 AS INT) > 0
                       AND m.invited_at >= NOW() - make_interval(hours => CAST($2 AS INT)))
                      OR (
                          CAST($4 AS BOOLEAN)
                          AND EXISTS (
                              SELECT 1 FROM pending_group_adds p
                              WHERE p.group_id = CAST($3 AS BIGINT)
                                AND p.member_id = m.inveted_user_id
                                AND p.qualified_at IS NULL
                          )
                      )
                  )
                """,
                parameters=(user_id, int(hold_hours), group_id, bool(check_quality)),
                fetchone=True,
            ),
        )
        return max(0, int((row or {}).get("total") or 0))

    # ======================================================
    # Капча перед выводом
    # ======================================================
    async def create_table_withdraw_captcha_state(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS withdraw_captcha_state (
                user_id          BIGINT PRIMARY KEY,
                failed_attempts  INT NOT NULL DEFAULT 0,
                locked_until     TIMESTAMPTZ NULL,
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

    async def get_captcha_lock_seconds(self, user_id: int) -> int:
        """Сколько секунд ещё действует блокировка вывода (0 — не заблокирован)."""
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                SELECT CAST(CEIL(EXTRACT(EPOCH FROM (locked_until - NOW()))) AS INT) AS sec
                FROM withdraw_captcha_state
                WHERE user_id = $1 AND locked_until > NOW()
                """,
                parameters=(user_id,),
                fetchone=True,
            ),
        )
        return max(0, int((row or {}).get("sec") or 0))

    async def register_captcha_failure(
        self, user_id: int, max_attempts: int, lock_seconds: int, window_seconds: int
    ) -> tuple[int, int]:
        """
        +1 неверный ответ. Ошибки старше window_seconds не учитываются.
        Возвращает (номер ошибки, на сколько секунд заблокирован вывод; 0 — не заблокирован).
        События одного пользователя обрабатываются по очереди (SmartRateLimiter),
        поэтому два запроса подряд здесь безопасны.
        """
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                INSERT INTO withdraw_captcha_state (user_id, failed_attempts, updated_at)
                VALUES ($1, 1, NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET failed_attempts = CASE
                        WHEN withdraw_captcha_state.updated_at < NOW() - make_interval(secs => CAST($2 AS INT))
                        THEN 1
                        ELSE withdraw_captcha_state.failed_attempts + 1
                    END,
                    updated_at = NOW()
                RETURNING failed_attempts
                """,
                parameters=(user_id, int(window_seconds)),
                fetchone=True,
            ),
        )
        attempts = int((row or {}).get("failed_attempts") or 1)
        if attempts < max_attempts:
            return attempts, 0
        await self.execute(
            """
            UPDATE withdraw_captcha_state
            SET failed_attempts = 0,
                locked_until = NOW() + make_interval(secs => CAST($2 AS INT)),
                updated_at = NOW()
            WHERE user_id = $1
            """,
            parameters=(user_id, int(lock_seconds)),
        )
        return attempts, int(lock_seconds)

    async def reset_captcha_failures(self, user_id: int) -> None:
        await self.execute(
            "UPDATE withdraw_captcha_state SET failed_attempts = 0, updated_at = NOW() WHERE user_id = $1",
            parameters=(user_id,),
        )

    # ======================================================
    # Добавленные участники: проверка перед зачётом
    # ======================================================
    async def create_table_pending_group_adds(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_group_adds (
                id            BIGSERIAL PRIMARY KEY,
                group_id      BIGINT NOT NULL,
                inviter_id    BIGINT NOT NULL,
                member_id     BIGINT NOT NULL,
                source        VARCHAR(16) NOT NULL,
                joined_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                qualified_at  TIMESTAMPTZ NULL,
                rejected_at   TIMESTAMPTZ NULL,
                reversed_at   TIMESTAMPTZ NULL,
                UNIQUE (group_id, member_id)
            );
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_adds_open "
            "ON pending_group_adds (group_id, inviter_id, joined_at) "
            "WHERE qualified_at IS NULL AND rejected_at IS NULL;"
        )

    async def create_table_group_member_activity(self) -> None:
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS group_member_activity (
                group_id         BIGINT NOT NULL,
                user_id          BIGINT NOT NULL,
                first_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_message_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                message_count    INT NOT NULL DEFAULT 1,
                PRIMARY KEY (group_id, user_id)
            );
            """
        )

    async def touch_group_activity(self, group_id: int, user_id: int) -> None:
        await self.execute(
            """
            INSERT INTO group_member_activity (group_id, user_id)
            VALUES (CAST($1 AS BIGINT), $2)
            ON CONFLICT (group_id, user_id) DO UPDATE
            SET last_message_at = NOW(),
                message_count = group_member_activity.message_count + 1
            """,
            parameters=(group_id, user_id),
        )

    async def add_pending_group_add(self, group_id: int, inviter_id: int, member_id: int, source: str) -> bool:
        """
        Запоминает «кто кого добавил» до проверки. Один человек в группе засчитывается
        один раз: повторное вступление и люди, засчитанные по старой схеме
        (group_manual_adds), второй раз не учитываются. False — уже был.
        """
        row = await self.execute(
            """
            INSERT INTO pending_group_adds (group_id, inviter_id, member_id, source)
            SELECT CAST($1 AS BIGINT), $2, $3, $4
            WHERE NOT EXISTS (
                SELECT 1 FROM group_manual_adds
                WHERE group_id = CAST($1 AS BIGINT) AND invited_user_id = $3
            )
            ON CONFLICT (group_id, member_id) DO NOTHING
            RETURNING id
            """,
            parameters=(group_id, inviter_id, member_id, source),
            fetchone=True,
        )
        return row is not None

    async def list_pending_adds(self, group_id: int, inviter_id: int, limit: int = 50):
        """Непроверенные добавленные пригласившего с сигналами из БД (старые — первыми)."""
        return await self.execute(
            """
            SELECT p.id,
                   p.member_id,
                   p.source,
                   EXTRACT(EPOCH FROM (NOW() - p.joined_at)) / 3600.0 AS hours_in_group,
                   (a.last_message_at IS NOT NULL AND a.last_message_at >= p.joined_at) AS wrote_in_group,
                   EXISTS (
                       SELECT 1 FROM user_activity ua
                       WHERE ua.user_id = p.member_id AND ua.event = 'bot_start'
                   ) AS started_bot
            FROM pending_group_adds p
            LEFT JOIN group_member_activity a
                   ON a.group_id = p.group_id AND a.user_id = p.member_id
            WHERE p.group_id = CAST($1 AS BIGINT)
              AND p.inviter_id = $2
              AND p.qualified_at IS NULL
              AND p.rejected_at IS NULL
            ORDER BY p.joined_at
            LIMIT $3
            """,
            parameters=(group_id, inviter_id, limit),
            fetchall=True,
        )

    async def count_pending_adds(self, group_id: int, inviter_id: int) -> int:
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                SELECT COUNT(*) AS count FROM pending_group_adds
                WHERE group_id = CAST($1 AS BIGINT) AND inviter_id = $2
                  AND qualified_at IS NULL AND rejected_at IS NULL
                """,
                parameters=(group_id, inviter_id),
                fetchone=True,
            ),
        )
        return self._count_from_row(row)

    async def qualify_pending_add(self, pending_id: int) -> bool:
        """Атомарно: запись проверена → +1 к «добавил N участников». Второй раз не засчитывает."""
        row = await self.execute(
            """
            WITH q AS (
                UPDATE pending_group_adds
                SET qualified_at = NOW()
                WHERE id = $1 AND qualified_at IS NULL AND rejected_at IS NULL
                RETURNING group_id, inviter_id
            ), cnt AS (
                INSERT INTO GroupMembers (group_id, user_id, add_user_count)
                SELECT group_id, inviter_id, 1 FROM q
                ON CONFLICT (group_id, user_id) DO UPDATE
                    SET add_user_count = GroupMembers.add_user_count + 1
                RETURNING user_id
            )
            SELECT inviter_id FROM q
            """,
            parameters=(pending_id,),
            fetchone=True,
        )
        return row is not None

    async def reject_pending_add(self, pending_id: int) -> None:
        await self.execute(
            """
            UPDATE pending_group_adds SET rejected_at = NOW()
            WHERE id = $1 AND qualified_at IS NULL AND rejected_at IS NULL
            """,
            parameters=(pending_id,),
        )

    async def release_pending_add_on_leave(self, group_id: int, member_id: int, hold_hours: int) -> int | None:
        """
        Добавленный вышел из группы:
        - не проверен — запись отклоняется, счётчик не трогаем (он не увеличивался);
        - проверен и добавлен вручную, вышел в срок hold_hours (0 — всегда) — -1 к
          «добавил N участников». Для вступивших по ссылке это делает
          reverse_referral_on_leave.
        Возвращает ID добавившего, если счётчик уменьшен.
        """
        await self.execute(
            """
            UPDATE pending_group_adds SET rejected_at = NOW()
            WHERE group_id = CAST($1 AS BIGINT) AND member_id = $2
              AND qualified_at IS NULL AND rejected_at IS NULL
            """,
            parameters=(group_id, member_id),
        )
        row = cast(
            dict[str, Any] | None,
            await self.execute(
                """
                WITH rev AS (
                    UPDATE pending_group_adds
                    SET reversed_at = NOW()
                    WHERE group_id = CAST($1 AS BIGINT)
                      AND member_id = $2
                      AND source = 'manual'
                      AND qualified_at IS NOT NULL
                      AND reversed_at IS NULL
                      AND (
                          CAST($3 AS INT) <= 0
                          OR joined_at >= NOW() - make_interval(hours => CAST($3 AS INT))
                      )
                    RETURNING group_id, inviter_id
                ), grp AS (
                    UPDATE GroupMembers gm
                    SET add_user_count = GREATEST(0, gm.add_user_count - 1)
                    FROM rev
                    WHERE gm.user_id = rev.inviter_id AND gm.group_id = rev.group_id
                    RETURNING gm.user_id
                )
                SELECT inviter_id FROM rev
                """,
                parameters=(group_id, member_id, int(hold_hours)),
                fetchone=True,
            ),
        )
        return int(row["inviter_id"]) if row else None

    async def reverse_referral_on_leave(self, invited_user_id: int, hold_hours: int, group_id: int):
        """
        Как в SafeDatabase, но:
        - «добавил N участников» уменьшается, только если этот человек был засчитан
          в счётчик: проверен (pending_group_adds.qualified_at) или засчитан по старой
          схеме (записи в pending_group_adds нет);
        - баланс НЕ обрезается до 0 (#46): если деньги за ушедшего уже выведены,
          остаётся долг (отрицательный balance_sum), и он гасится следующими
          начислениями. Раньше GREATEST(0, …) списывал потерю на владельца бота.
        Все CTE видят одно состояние таблиц, поэтому порядок с
        release_pending_add_on_leave не важен.
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
                    balance_sum   = uic.balance_sum - rev.reward_sum,
                    earned_sum    = GREATEST(0, uic.earned_sum - rev.reward_sum)
                FROM rev
                WHERE uic.user_id = rev.user_id
                RETURNING uic.user_id
            ), grp AS (
                UPDATE GroupMembers gm
                SET add_user_count = GREATEST(0, gm.add_user_count - 1)
                FROM rev
                WHERE gm.user_id = rev.user_id
                  AND gm.group_id = CAST($3 AS BIGINT)
                  AND NOT EXISTS (
                      SELECT 1 FROM pending_group_adds p
                      WHERE p.group_id = CAST($3 AS BIGINT)
                        AND p.member_id = $1
                        AND p.qualified_at IS NULL
                  )
                RETURNING gm.user_id
            )
            SELECT user_id, reward_sum FROM rev
            """,
            parameters=(invited_user_id, int(hold_hours), group_id),
            fetchone=True,
        )
