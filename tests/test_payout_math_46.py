"""Тесты доработок антифрода (#46). Запуск: python -m pytest tests"""
import pytest
from _load import load

pm = load("utils/payout_math.py", "payout_math_46_under_test")
sql = load("utils/db/unverified_sql.py", "unverified_sql_under_test")


@pytest.mark.parametrize(
    ("balance", "expected"),
    [
        (50000, 0),     # положительный остаток обнуляется
        (0, 0),
        (-2500, -2500),  # долг «Обнулить» больше не стирает
    ],
)
def test_balance_after_reset_keeps_debt(balance, expected):
    assert pm.balance_after_reset(balance) == expected


def test_reasons_locked_money_blocks_approval():
    # Баланс хватает, но часть денег ещё на проверке — одобрять нельзя
    reasons = pm.approval_rejection_reasons(balance=100000, locked=60000, added=5, amount=50000, required_members=5)
    assert len(reasons) == 1
    assert "на проверке" in reasons[0]
    assert "40000" in reasons[0]


def test_reasons_low_balance_and_members():
    reasons = pm.approval_rejection_reasons(balance=1000, locked=0, added=2, amount=50000, required_members=5)
    assert len(reasons) == 2
    assert "1000" in reasons[0]
    assert "2 из 5" in reasons[1]


def test_reasons_fallback_when_conditions_met():
    reasons = pm.approval_rejection_reasons(balance=100000, locked=0, added=5, amount=50000, required_members=5)
    assert reasons == ["• условия изменились во время подтверждения, повторите попытку"]


def test_reasons_negative_locked_is_ignored():
    reasons = pm.approval_rejection_reasons(balance=100000, locked=-500, added=0, amount=100000, required_members=0)
    assert "повторите попытку" in reasons[0]


def test_unverified_sql_excludes_rejected():
    # Отклонённые проверкой не должны висеть «на проверке» вечно
    q = sql.unverified_sum_sql("$1", "CAST($2 AS INT)", "CAST($3 AS BIGINT)", "CAST($4 AS BOOLEAN)")
    assert "p.rejected_at IS NULL" in q
    assert "p.qualified_at IS NULL" in q
    assert "m.reversed_at IS NULL" in q
    assert "m.user_id = $1" in q
