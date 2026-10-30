# utils/captcha.py
"""
Капча перед выводом средств (#18).

Раньше было «a + b» из цифр 1–9: всего 17 возможных ответов, попыток сколько
угодно, ответ после ошибки не менялся — перебор занимал секунды.
Теперь:
- примеры на +, −, × с двузначными числами (ответов сотни, угадать нельзя);
- после каждой ошибки — новый пример;
- MAX_ATTEMPTS ошибок подряд блокируют вывод на LOCK_SECONDS. Счётчик хранится
  в БД (withdraw_captcha_state), поэтому /start и перезапуск бота его не сбрасывают;
- на ответ даётся TTL_SECONDS (1 минута), потом выдаётся новый пример.

Модуль без зависимостей от aiogram и БД, чтобы его было просто тестировать.
"""
from __future__ import annotations

import random
import secrets
from dataclasses import dataclass

MAX_ATTEMPTS = 3
LOCK_SECONDS = 15 * 60
# Одна минута на ответ: человеку хватает, а решать пример «на стороне» уже неудобно
TTL_SECONDS = 60
# Ошибки старше этого окна не копятся: две ошибки месяц назад не должны
# блокировать вывод с первой ошибки сегодня
FAIL_WINDOW_SECONDS = 60 * 60
_MAX_ANSWER_DIGITS = 4

_system_rng = secrets.SystemRandom()


@dataclass(frozen=True)
class Captcha:
    question: str
    answer: int


def generate(rng: random.Random | None = None) -> Captcha:
    r = rng or _system_rng
    op = r.choice(("+", "-", "*"))
    if op == "+":
        a, b = r.randint(10, 49), r.randint(10, 49)
        return Captcha(f"{a} + {b}", a + b)
    if op == "-":
        a, b = r.randint(10, 49), r.randint(10, 49)
        if a < b:
            a, b = b, a
        return Captcha(f"{a} − {b}", a - b)
    a, b = r.randint(10, 49), r.randint(2, 9)
    return Captcha(f"{a} × {b}", a * b)


def parse_answer(text: str | None) -> int | None:
    """Число из ответа пользователя или None, если это не число (такая попытка не считается)."""
    raw = (text or "").strip().replace(" ", "")
    if not raw or len(raw) > _MAX_ANSWER_DIGITS or not (raw.isascii() and raw.isdigit()):
        return None
    return int(raw)


def is_expired(issued_at: float | None, now: float) -> bool:
    return issued_at is None or now - float(issued_at) > TTL_SECONDS


def time_limit_text() -> str:
    """Сколько времени на ответ — для текста сообщения ("1 мин." / "30 сек.")."""
    if TTL_SECONDS % 60 == 0:
        return f"{TTL_SECONDS // 60} мин."
    return f"{TTL_SECONDS} сек."
