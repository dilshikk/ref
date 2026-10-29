"""Тесты денежной логики баланса и выплат (#24, #46). Запуск: python -m pytest tests"""
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


@pytest.mark.parametrize(
    ("balance", "locked", "pending", "expected"),
    [
        (100000, 0, 0, 100000),
        # #46: 200 свежих рефералов по 500 — вывести нельзя ничего, пока они не проверены
        (100000, 100000, 0, 0),
        (100000, 60000, 0, 40000),
        (100000, 60000, 30000, 10000),
        (100000, 60000, 50000, 0),
        # долг после выхода приглашённых: вывести нечего
        (-2500, 0, 0, 0),
        (-2500, 1000, 0, 0),
        # мусорные отрицательные значения не увеличивают доступное
        (5000, -100, -100, 5000),
    ],
)
def test_withdrawable(balance, locked, pending, expected):
    assert pm.withdrawable(balance, locked, pending) == expected
