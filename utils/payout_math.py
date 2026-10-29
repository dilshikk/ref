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


def withdrawable(balance_sum: int, locked: int, pending: int) -> int:
    """
    Сколько можно вывести (#46): баланс минус деньги «на проверке» (за приглашённых,
    которые ещё могут выйти из группы или не прошли проверку живости) минус ожидающие
    заявки. Отрицательный баланс — долг после выхода приглашённых — даёт 0.
    """
    return max(0, balance_sum - max(0, locked) - max(0, pending))


def balance_after_reset(balance_sum: int) -> int:
    """«Обнулить» (#46): положительный остаток → 0, долг сохраняется (как LEAST(balance_sum, 0) в SQL)."""
    return min(balance_sum, 0)


def approval_rejection_reasons(
    balance: int, locked: int, added: int, amount: int, required_members: int
) -> list[str]:
    """Почему выплату нельзя одобрить — те же условия, что в approve_payout_if_pending."""
    locked = max(0, locked)
    reasons: list[str] = []
    if balance < amount:
        reasons.append(
            f"• баланс пользователя сейчас {balance} сум при сумме заявки {amount} сум "
            "(часть приглашённых, скорее всего, вышла из группы)"
        )
    elif balance - locked < amount:
        reasons.append(
            f"• из баланса {balance} сум ещё {locked} сум на проверке (приглашённые недавно "
            f"вступили или не прошли проверку): доступно {max(0, balance - locked)} сум "
            f"при сумме заявки {amount} сум"
        )
    if required_members > 0 and added < required_members:
        reasons.append(f"• добавлено участников: {added} из {required_members} (кто-то вышел из группы)")
    if not reasons:
        reasons.append("• условия изменились во время подтверждения, повторите попытку")
    return reasons
