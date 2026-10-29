"""Тесты денежной логики баланса и выплат (#24). Запуск: python -m pytest tests"""
import pytest
from _load import load

pm = load("utils/payout_math.py", "payout_math_under_test")


@pytest.mark.parametrize(
    ("amount", "price", "expected"),
    [
        (5000, 500, 10),   # ровно
        (5001, 500, 11),   # остаток округляется вверх: недоплаты рефералами нет
        (499, 500, 1),
        (500, 500, 1),
        (0, 500, 0),
        (-100, 500, 0),
        (5000, 0, 0),      # цена 0 — не делим на ноль
        (5000, -10, 0),
    ],
)
def test_credits_for_amount(amount, price, expected):
    assert pm.credits_for_amount(amount, price) == expected


def test_pending_total_ignores_none_and_garbage():
    assert pm.pending_total([]) == 0
    assert pm.pending_total([None, 1000, "2500", 0]) == 3500
    assert pm.pending_total(["abc", object(), 100]) == 100
    assert pm.pending_total([100.7, 0.5]) == 101


@pytest.mark.parametrize(
    ("balance", "pending", "expected"),
    [(10000, 0, 10000), (10000, 4000, 6000), (10000, 10000, 0), (3000, 5000, 0)],
)
def test_available_never_negative(balance, pending, expected):
    assert pm.available_balance(balance, pending) == expected
