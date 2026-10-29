"""Тесты проверки добавленных участников (#18). Запуск: python -m pytest tests"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).resolve().parent.parent / "utils" / "account_quality.py"
_spec = importlib.util.spec_from_file_location("account_quality_under_test", _PATH)
aq = importlib.util.module_from_spec(_spec)
# dataclass с from __future__ import annotations ищет модуль в sys.modules
sys.modules[_spec.name] = aq
_spec.loader.exec_module(aq)


def _signals(**over):
    base = dict(hours_in_group=30, still_member=True, wrote_in_group=False, started_bot=False,
                has_photo=False, has_username=False, is_premium=False)
    base.update(over)
    return aq.MemberSignals(**base)


def test_empty_fresh_account_is_not_counted():
    # Типичный аккаунт для накрутки: ничего не делает
    assert aq.evaluate(_signals(), 24, 2) == "wait"


def test_active_member_is_counted():
    assert aq.evaluate(_signals(wrote_in_group=True), 24, 2) == "qualified"
    assert aq.evaluate(_signals(started_bot=True), 24, 2) == "qualified"
    assert aq.evaluate(_signals(has_photo=True, has_username=True), 24, 2) == "qualified"


def test_profile_alone_is_not_enough():
    assert aq.evaluate(_signals(has_photo=True), 24, 2) == "wait"


def test_must_stay_min_hours():
    assert aq.evaluate(_signals(hours_in_group=23.9, wrote_in_group=True), 24, 2) == "wait"
    assert aq.evaluate(_signals(hours_in_group=24, wrote_in_group=True), 24, 2) == "qualified"


def test_left_member_is_rejected():
    assert aq.evaluate(_signals(still_member=False, wrote_in_group=True), 24, 2) == "rejected"


def test_disabled_check():
    assert not aq.is_enabled(0, 0)
    assert aq.is_enabled(24, 0) and aq.is_enabled(0, 2)
    assert aq.evaluate(_signals(hours_in_group=0), 0, 0) == "qualified"


def test_member_status():
    assert aq.is_member_status(SimpleNamespace(status="member"))
    assert aq.is_member_status(SimpleNamespace(status="restricted", is_member=True))
    assert not aq.is_member_status(SimpleNamespace(status="restricted", is_member=False))
    assert not aq.is_member_status(SimpleNamespace(status="left"))
    assert not aq.is_member_status(SimpleNamespace(status="kicked"))
