# utils/payout_math.py
"""
Денежные расчёты баланса и выплат (#24) без зависимостей от бота и БД,
чтобы их можно было покрыть тестами.
"""
from collections.abc import Iterable


def credits_for_amount(amount: int, invited_credit: int) -> int:
    """Сколько «приглашений» списать за выплату amount: деление с округлением вверх.
    При цене 0 списывать нечего (иначе деление на ноль)."""
    if amount <= 0 or invited_credit <= 0:
        return 0
    return -(-amount // invited_credit)


def pending_total(amounts: Iterable[object]) -> int:
    """Сумма ожидающих заявок. None и мусор считаются нулём, дробные копейки отбрасываются."""
    total = 0.0
    for raw in amounts:
        try:
            total += float(raw or 0)
        except (TypeError, ValueError):
            continue
    return int(total)


def available_balance(balance_sum: int, pending: int) -> int:
    """Доступно к выводу: заявка списывается только при одобрении, поэтому
    ожидающие выплаты вычитаем, чтобы не показывать одни деньги дважды."""
    return max(0, balance_sum - pending)
