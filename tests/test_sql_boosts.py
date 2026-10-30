"""
SQL-тесты награды за голос (буст) канала на настоящем PostgreSQL.

Запускаются только при TEST_DATABASE=1 (в CI поднимается postgres:16), как и
tests/test_sql_money.py. ВНИМАНИЕ: тесты очищают таблицы — НЕ указывайте рабочую базу.
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
CHANNEL = -1009876543210
USER = 777
REWARD = 300

_TEST_ENV = {
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "ADMINS": "901,902",
    "MONEY_ADMIN": "901",
    "GROUP_ID": "-1001234567890",
    "URL_YML_PATH": str(ROOT / "data" / "url.yml.example"),
}


@pytest.fixture(scope="module")
def db():
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
    async def wrapper():
        await db.create_pool()
        try:
            await db.execute("TRUNCATE boost_rewards, UserInviteCount RESTART IDENTITY")
            await body()
        finally:
            await db.close_pool()

    asyncio.run(wrapper())


async def money(db, user_id=USER):
    """(баланс, заработано)."""
    row = await db.get_user_invited_count(user_id)
    return (int(row["balance_sum"]), int(row["earned_sum"])) if row else (0, 0)


def test_boost_is_paid_once_per_person(db):
    async def body():
        assert await db.reward_boost_once(USER, CHANNEL, "b1", REWARD) is not None
        # Второй буст того же человека (Premium) и повторное событие — без оплаты
        assert await db.reward_boost_once(USER, CHANNEL, "b2", REWARD) is None
        assert await db.reward_boost_once(USER, CHANNEL, "b1", REWARD) is None
        # Два одновременных события другого человека
        results = await asyncio.gather(*(db.reward_boost_once(888, CHANNEL, "c1", REWARD) for _ in range(2)))
        assert sum(r is not None for r in results) == 1
        assert await money(db) == (REWARD, REWARD)
        assert await money(db, 888) == (REWARD, REWARD)

    run(db, body)


def test_removed_boost_within_hold_is_reversed_once(db):
    async def body():
        await db.reward_boost_once(USER, CHANNEL, "b1", REWARD)
        row = await db.reverse_boost_reward(CHANNEL, "b1", 72)
        assert row is not None and int(row["reward_sum"]) == REWARD
        assert await money(db) == (0, 0)
        assert await db.reverse_boost_reward(CHANNEL, "b1", 72) is None
        # Поставил буст снова — второй раз не платим
        assert await db.reward_boost_once(USER, CHANNEL, "b3", REWARD) is None
        assert await money(db) == (0, 0)

    run(db, body)


def test_removed_boost_after_hold_keeps_money(db):
    async def body():
        await db.reward_boost_once(USER, CHANNEL, "b1", REWARD)
        await db.execute(
            "UPDATE boost_rewards SET rewarded_at = NOW() - INTERVAL '100 hours' WHERE user_id = $1",
            parameters=(USER,),
        )
        assert await db.reverse_boost_reward(CHANNEL, "b1", 72) is None
        assert await money(db) == (REWARD, REWARD)
        # 0 часов — снимается всегда
        assert await db.reverse_boost_reward(CHANNEL, "b1", 0) is not None
        assert await money(db) == (0, 0)

    run(db, body)


def test_reverse_after_payout_leaves_debt(db):
    async def body():
        await db.reward_boost_once(USER, CHANNEL, "b1", REWARD)
        await db.execute("UPDATE UserInviteCount SET balance_sum = 0 WHERE user_id = $1", parameters=(USER,))
        assert await db.reverse_boost_reward(CHANNEL, "b1", 0) is not None
        assert (await money(db))[0] == -REWARD
        stats = await db.boost_reward_stats()
        assert stats == {"total": 1, "active": 0}

    run(db, body)


def test_other_channel_boost_is_not_reversed(db):
    async def body():
        await db.reward_boost_once(USER, CHANNEL, "b1", REWARD)
        assert await db.reverse_boost_reward(-100111, "b1", 0) is None
        assert await money(db) == (REWARD, REWARD)

    run(db, body)
