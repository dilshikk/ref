# utils/payment_details.py
"""
Реквизиты выплат (#20): проверка, маскирование и шифрование.

- Карта: 16 цифр + проверка по алгоритму Луна (ловит опечатки).
- Телефон: только Узбекистан, +998 и код мобильного оператора.
- Шифрование: Fernet (библиотека cryptography), ключ в PAYOUT_ENCRYPTION_KEY.
  Зашифрованное значение хранится с префиксом "enc:", поэтому старые
  незашифрованные заявки продолжают читаться как есть.
  Без ключа бот работает как раньше (открытым текстом) и пишет предупреждение.
- Формы вывода в fsm_storage (#44): seal_form/open_form шифруют карту и имя
  держателя перед записью в базу и расшифровывают при чтении.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from loguru import logger

ENC_PREFIX = "enc:"
UNREADABLE = "⚠️ не расшифровано"

# Поля формы вывода, которые нельзя хранить в fsm_storage открытым текстом (#44)
SENSITIVE_FORM_FIELDS: tuple[str, ...] = ("payment_info", "card_holder")

# Коды мобильных операторов Узбекистана (после 998)
UZ_MOBILE_CODES = frozenset(
    {"20", "33", "50", "55", "77", "88", "90", "91", "93", "94", "95", "97", "98", "99"}
)


# ======================================================
# Проверка
# ======================================================
def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def luhn_valid(number: str) -> bool:
    """Контрольная сумма Луна: удваиваем каждую вторую цифру справа."""
    if not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def normalize_card(raw: str) -> str | None:
    """Номер карты без пробелов и дефисов или None, если номер неверный."""
    cleaned = raw.strip().replace(" ", "").replace("-", "")
    if len(cleaned) != 16 or not cleaned.isdigit():
        return None
    return cleaned if luhn_valid(cleaned) else None


def normalize_uz_phone(raw: str) -> str | None:
    """Телефон в виде +998XXXXXXXXX или None, если формат или оператор неверные."""
    cleaned = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if len(cleaned) != 12 or not cleaned.isdigit() or not cleaned.startswith("998"):
        return None
    if cleaned[3:5] not in UZ_MOBILE_CODES:
        return None
    return f"+{cleaned}"


def mask(value: str | None) -> str:
    """Для журналов и интерфейса: только последние 4 цифры."""
    digits = _digits_only(value or "")
    return f"****{digits[-4:]}" if len(digits) >= 4 else "—"


# ======================================================
# Шифрование
# ======================================================
@lru_cache(maxsize=1)
def _fernet() -> Any | None:
    key = os.getenv("PAYOUT_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "⚠️ PAYOUT_ENCRYPTION_KEY не задан: номера карт сохраняются открытым текстом. "
            "Создайте ключ: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return None
    from cryptography.fernet import Fernet

    # Неверный ключ должен остановить запуск, а не тихо писать открытым текстом
    return Fernet(key.encode())


def encryption_enabled() -> bool:
    return _fernet() is not None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENC_PREFIX)


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None or is_encrypted(value):
        return value
    return ENC_PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str:
    if value is None:
        return ""
    if not is_encrypted(value):
        return value  # старая заявка до включения шифрования
    f = _fernet()
    if f is None:
        logger.error("Найдены зашифрованные реквизиты, но PAYOUT_ENCRYPTION_KEY не задан")
        return UNREADABLE
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Не удалось расшифровать реквизиты: ключ PAYOUT_ENCRYPTION_KEY сменился?")
        return UNREADABLE


# ======================================================
# Формы вывода в fsm_storage (#44)
# ======================================================
def seal_form(data: Mapping[str, Any]) -> dict[str, Any]:
    """Копия данных формы, в которой карта и имя держателя зашифрованы (для записи в БД)."""
    sealed = dict(data)
    for field in SENSITIVE_FORM_FIELDS:
        value = sealed.get(field)
        if isinstance(value, str) and value:
            sealed[field] = encrypt(value)
    return sealed


def open_form(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Копия данных формы с расшифрованными реквизитами.
    Нерасшифровываемое поле удаляется: лучше «Сессия устарела, начните заново»,
    чем заявка с «⚠️ не расшифровано» вместо номера карты.
    """
    opened = dict(data)
    for field in SENSITIVE_FORM_FIELDS:
        value = opened.get(field)
        if not isinstance(value, str):
            continue
        plain = decrypt(value)
        if plain == UNREADABLE:
            opened.pop(field)
        else:
            opened[field] = plain
    return opened
