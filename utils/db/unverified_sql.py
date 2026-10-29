# utils/db/unverified_sql.py
"""
Один SQL-фрагмент «сумма на проверке» (#46) для трёх мест:
показ пользователю, подача заявки и одобрение выплаты админом.
Раньше одобрение проверяло только balance_sum >= amount.

В фрагмент подставляются только выражения из кода (плейсхолдеры $N и имена
колонок), а не пользовательский ввод.
"""
from __future__ import annotations


def unverified_sum_sql(user_expr: str, hold_expr: str, group_expr: str, quality_expr: str) -> str:
    """
    Подзапрос: сумма начислений, которые ещё нельзя вывести.
    - не прошло hold_hours с вступления — выход приглашённого ещё снимет начисление;
    - приглашённый ждёт проверки живости (если проверка включена).
    Отклонённые проверкой (rejected_at) сюда не попадают: их начисление снимается
    при отклонении (reject_pending_add), а не висит «на проверке» вечно.
    """
    return f"""(
        SELECT COALESCE(SUM(m.reward_sum), 0)::bigint
        FROM UserInviteMember m
        WHERE m.user_id = {user_expr}
          AND m.reversed_at IS NULL
          AND (
              ({hold_expr} > 0
               AND m.invited_at >= NOW() - make_interval(hours => {hold_expr}))
              OR (
                  {quality_expr}
                  AND EXISTS (
                      SELECT 1 FROM pending_group_adds p
                      WHERE p.group_id = {group_expr}
                        AND p.member_id = m.inveted_user_id
                        AND p.qualified_at IS NULL
                        AND p.rejected_at IS NULL
                  )
              )
          )
    )"""
