# utils/subscription_status.py
"""
Итог проверки подписки по ответам Telegram на каждый канал (#24).

Раньше любая ошибка проверки считалась подпиской. Теперь ошибки различаются:
- "config" — канал настроен неверно (бот не админ, канал удалён). Такой канал
  пропускается при проверке доступа к боту, чтобы ошибка настройки не блокировала
  всех пользователей, а в строгом режиме (начисление денег) считается неподпиской;
- "error" — сбой сети или Telegram. Подписка НЕ засчитывается: итог UNKNOWN,
  его не кэшируют и проверяют снова при следующем сообщении.
"""
from collections.abc import Iterable
from enum import Enum
from typing import Literal

Outcome = Literal["member", "not_member", "config", "error"]


class SubscriptionStatus(str, Enum):
    SUBSCRIBED = "subscribed"
    NOT_SUBSCRIBED = "not_subscribed"
    UNKNOWN = "unknown"  # проверить не удалось — повторить позже


def combine(outcomes: Iterable[Outcome], *, strict: bool = False) -> SubscriptionStatus:
    """Неподписка хотя бы на один канал важнее сбоя: о ней можно сказать точно."""
    has_error = False
    for outcome in outcomes:
        if outcome == "not_member":
            return SubscriptionStatus.NOT_SUBSCRIBED
        if outcome == "config":
            if strict:
                return SubscriptionStatus.NOT_SUBSCRIBED
            continue
        if outcome == "error":
            has_error = True
    return SubscriptionStatus.UNKNOWN if has_error else SubscriptionStatus.SUBSCRIBED
