"""Тесты капчи перед выводом (#18). Запуск: python -m pytest tests"""
import importlib.util
import random
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "utils" / "captcha.py"
_spec = importlib.util.spec_from_file_location("captcha_under_test", _PATH)
captcha = importlib.util.module_from_spec(_spec)
# dataclass с from __future__ import annotations ищет модуль в sys.modules
sys.modules[_spec.name] = captcha
_spec.loader.exec_module(captcha)

_OPS = {"+": lambda a, b: a + b, "−": lambda a, b: a - b, "×": lambda a, b: a * b}


def test_answer_matches_question():
    rng = random.Random(7)
    for _ in range(2000):
        c = captcha.generate(rng)
        a, op, b = c.question.split(" ")
        assert _OPS[op](int(a), int(b)) == c.answer
        assert c.answer >= 0


def test_answers_are_not_guessable():
    # Раньше «a + b» из 1–9 давало всего 17 ответов
    rng = random.Random(1)
    answers = {captcha.generate(rng).answer for _ in range(3000)}
    assert len(answers) > 150


def test_parse_answer():
    assert captcha.parse_answer(" 42 ") == 42
    assert captcha.parse_answer("4 2") == 42
    for bad in (None, "", "abc", "-3", "4.5", "²", "12345"):
        assert captcha.parse_answer(bad) is None


def test_expiry():
    assert captcha.is_expired(None, 100)
    assert not captcha.is_expired(0, captcha.TTL_SECONDS)
    assert captcha.is_expired(0, captcha.TTL_SECONDS + 1)


def test_one_minute_to_answer():
    assert captcha.TTL_SECONDS == 60
    assert not captcha.is_expired(1000, 1059)
    assert captcha.is_expired(1000, 1061)
    assert captcha.time_limit_text() == "1 мин."


def test_limits():
    assert captcha.MAX_ATTEMPTS == 3
    assert captcha.LOCK_SECONDS == 15 * 60
