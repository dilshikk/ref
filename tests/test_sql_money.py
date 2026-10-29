"""
SQL-тесты денежной логики на настоящем PostgreSQL (#48).

Раньше тестами была покрыта только чистая логика, а CTE-запросы из utils/db/*.py
(одобрение выплаты, начисление, снятие при выходе, удержания, проверка добавленных)
никто не выполнял: ошибку в SQL CI бы не заметил.

Запускаются только при TEST_DATABASE=1 (в CI поднимается postgres:16).
Локально:
    docker run --rm -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=referral_test -p 5432:5432 postgres:16
    TEST_DATABASE=1 DB_HOST=localhost DB_USER=postgres DB_PASS=postgres DB_NAME=referral_test \
        python -m pytest tests -q
ВНИМАНИЕ: тесты очищают таблицы — НЕ указывайте рабочую базу.
"""
import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE") != "1",
    reason="нужен PostgreSQL: задайте TEST_DATABASE=1 и DB_*",
)

ROOT = Path(__file__).resolve().parent.parent
GROUP = -1001234567890
PRICE = 500
REFERRER = 111
ADMIN_A = 901
ADMIN_B = 902

# data.config требует эти переменные при импорте. Запросов в Telegram тесты не делают.
_TEST_ENV = {
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "ADMINS": f"{ADMIN_A},{ADMIN_B}",
    "MONEY_ADMIN": str(ADMIN_A),
    "GROUP_ID": str(GROUP),
    "URL_YML_PATH": str(ROOT / "data" / "url.yml.example"),
}

_TABLES = (
    "UserInviteCount, UserInviteMember, GroupMembers, PayoutRequests, "
    "referral_holds, pending_group_adds, group_manual_adds"
)


@pytest.fixture(scope="module")
def db():
    """Создаёт все таблицы и миграции тем же кодом, что и при старте бота."""
    for key, value in _TEST_ENV.items():
        os.environ.setdefault(key, value)
    import app
    from loader import db as database

    async def setup():
        await database.create_pool()
        try:
            await app.database_connected()
        finally:
            await database.close_pool()

    asyncio.run(setup())
    return database


def run(db, body):
    """Каждый тест — в своём event loop: пул, чистые таблицы, тело теста, закрытие пула."""
    async def wrapper():
        await db.create_pool()
        try:
            await db.execute(f"TRUNCATE {_TABLES} RESTART IDENTITY")
            await body()
        finally:
            await db.close_pool()

    asyncio.run(wrapper())


async def balance(db, user_id=REFERRER):
    """(счётчик приглашённых, баланс в деньгах)."""
    row = await db.get_user_invited_count(user_id)
    return (int(row["current_count"]), int(row["balance_sum"])) if row else (0, 0)


async def added(db, user_id=REFERRER):
    """«Добавил N участников» в группе GROUP."""
    row = await db.execute(
        "SELECT add_user_count FROM GroupMembers WHERE group_id = CAST($1 AS BIGINT) AND user_id = $2",
        parameters=(GROUP, user_id),
        fetchone=True,
    )
    return int(row["add_user_count"]) if row else 0


async def set_added(db, count, user_id=REFERRER):
    await db.execute(
        "INSERT INTO GroupMembers (group_id, user_id, add_user_count) VALUES (CAST($1 AS BIGINT), $2, $3)",
        parameters=(GROUP, user_id, count),
    )


async def payout(db, amount, user_id=REFERRER):
    request_id = await db.add_payout_request(user_id, "Test User", None, "8600000000000000", float(amount))
    assert request_id is not None
    return request_id


async def approve(db, request_id, *, hold_hours=0, members=0):
    return await db.approve_payout_if_pending(
        request_id,
        credits_to_deduct=1,
        group_id=GROUP,
        members_to_deduct=members,
        hold_hours=hold_hours,
        check_quality=False,
    )


async def status(db, request_id):
    row = await db.get_payout_request_by_id(request_id)
    return row["status"]


# ======================================================
# Начисление за приглашённого
# ======================================================
def test_credit_referral_once_counts_once(db):
    async def body():
        assert await db.credit_referral_once(REFERRER, 201, PRICE) is True
        assert await db.credit_referral_once(REFERRER, 201, PRICE) is False
        # Два одновременных события Telegram о вступлении одного человека
        results = await asyncio.gather(*(db.credit_referral_once(REFERRER, 202, PRICE) for _ in range(2)))
        assert sorted(results) == [False, True]
        assert await balance(db) == (2, 2 * PRICE)

    run(db, body)


# ======================================================
# Одобрение выплаты
# ======================================================
def test_double_approve_deducts_once(db):
    async def body():
        await db.add_balance(REFERRER, 2, price=1000)
        request_id = await payout(db, 500)
        assert await approve(db, request_id) is not None
        assert await approve(db, request_id) is None
        assert await status(db, request_id) == "approved"
        assert await balance(db) == (1, 1500)

    run(db, body)


def test_parallel_approve_deducts_once(db):
    async def body():
        await db.add_balance(REFERRER, 2, price=1000)
        request_id = await payout(db, 500)
        results = await asyncio.gather(approve(db, request_id), approve(db, request_id))
        assert sum(r is not None for r in results) == 1
        assert await balance(db) == (1, 1500)

    run(db, body)


def test_approve_rejected_when_balance_too_low(db):
    async def body():
        await db.add_balance(REFERRER, 1, price=1000)
        request_id = await payout(db, 5000)
        assert await approve(db, request_id) is None
        assert await status(db, request_id) == "pending"
        assert await balance(db) == (1, 1000)
        state = await db.get_payout_approval_state(request_id, GROUP, 0, False)
        assert int(state["balance_sum"]) == 1000

    run(db, body)


def test_approve_respects_locked_money(db):
    # #46: деньги за недавно приглашённых (срок удержания не прошёл) выводить нельзя
    async def body():
        await db.credit_referral_once(REFERRER, 201, PRICE)
        await db.credit_referral_once(REFERRER, 202, PRICE)
        request_id = await payout(db, 2 * PRICE)
        assert await approve(db, request_id, hold_hours=72) is None
        state = await db.get_payout_approval_state(request_id, GROUP, 72, False)
        assert state["status"] == "pending"
        assert int(state["locked_sum"]) == 2 * PRICE
        # Без срока удержания те же деньги выводятся
        assert await approve(db, request_id, hold_hours=0) is not None
        assert await balance(db) == (1, 0)

    run(db, body)


def test_approve_checks_and_deducts_added_members(db):
    async def body():
        await db.add_balance(REFERRER, 1, price=1000)
        await set_added(db, 2)
        request_id = await payout(db, 1000)
        assert await approve(db, request_id, members=3) is None
        assert await added(db) == 2
        await db.execute(
            "UPDATE GroupMembers SET add_user_count = 4 WHERE user_id = $1",
            parameters=(REFERRER,),
        )
        assert await approve(db, request_id, members=3) is not None
        assert await added(db) == 1  # лишний добавленный сохраняется

    run(db, body)


# ======================================================
# Выход приглашённого из группы
# ======================================================
def test_leave_before_payout_reverses_credit(db):
    async def body():
        await set_added(db, 3)
        await db.credit_referral_once(REFERRER, 201, PRICE)
        row = await db.reverse_referral_on_leave(201, 72, GROUP)
        assert row is not None and int(row["reward_sum"]) == PRICE
        assert await balance(db) == (0, 0)
        assert await added(db) == 2
        # Второй раз снимать нечего, повторное вступление денег не даёт
        assert await db.reverse_referral_on_leave(201, 72, GROUP) is None
        assert await db.credit_referral_once(REFERRER, 201, PRICE) is False
        assert await balance(db) == (0, 0)

    run(db, body)


def test_leave_after_payout_leaves_debt(db):
    # #46: если деньги уже выведены, остаётся долг, а не баланс 0
    async def body():
        await db.credit_referral_once(REFERRER, 201, PRICE)
        request_id = await payout(db, PRICE)
        assert await approve(db, request_id) is not None
        assert await balance(db) == (0, 0)
        assert await db.reverse_referral_on_leave(201, 0, GROUP) is not None
        assert await balance(db) == (0, -PRICE)

    run(db, body)


# ======================================================
# Удержания сверх антифрод-лимита
# ======================================================
def test_parallel_hold_approval_credits_once(db):
    async def body():
        for i in range(3):
            assert await db.hold_referral(REFERRER, 300 + i, f"user{i}", GROUP) is True
        assert await db.hold_referral(REFERRER, 300, "user0", GROUP) is False
        results = await asyncio.gather(
            db.approve_referral_holds(REFERRER, ADMIN_A, PRICE, GROUP),
            db.approve_referral_holds(REFERRER, ADMIN_B, PRICE, GROUP),
        )
        assert sum(r["claimed"] for r in results) == 3
        assert sum(r["credited"] for r in results) == 3
        assert await balance(db) == (3, 3 * PRICE)
        assert await added(db) == 3
        assert await db.list_pending_referral_holds() == []

    run(db, body)


# ======================================================
# Проверка добавленных участников
# ======================================================
def test_qualify_pending_add_once_and_release_on_leave(db):
    async def body():
        assert await db.add_pending_group_add(GROUP, REFERRER, 401, "manual") is True
        assert await db.add_pending_group_add(GROUP, REFERRER, 401, "manual") is False
        pending = await db.list_pending_adds(GROUP, REFERRER)
        assert len(pending) == 1
        pending_id = int(pending[0]["id"])
        assert await db.qualify_pending_add(pending_id) is True
        assert await db.qualify_pending_add(pending_id) is False
        assert await added(db) == 1
        assert await db.count_pending_adds(GROUP, REFERRER) == 0
        # Проверенный, добавленный вручную, вышел в срок — счётчик уменьшается один раз
        assert await db.release_pending_add_on_leave(GROUP, 401, 72) == REFERRER
        assert await added(db) == 0
        assert await db.release_pending_add_on_leave(GROUP, 401, 72) is None

    run(db, body)


def test_unverified_referral_leaving_loses_credit(db):
    async def body():
        await db.credit_referral_once(REFERRER, 501, PRICE)
        assert await db.add_pending_group_add(GROUP, REFERRER, 501, "referral") is True
        # Непроверенный в «добавил N» не входил — счётчик не трогается
        assert await db.release_pending_add_on_leave(GROUP, 501, 72) is None
        assert await added(db) == 0
        assert await db.count_pending_adds(GROUP, REFERRER) == 0
        # Начисление снято, повторно снимать нечего
        assert await balance(db) == (0, 0)
        assert await db.reverse_referral_on_leave(501, 72, GROUP) is None

    run(db, body)
