"""Разбор ссылки на буст и настройки награды за голос за канал.
Только чистые функции и разбор исходников — без aiogram и базы. Запуск: python -m pytest tests"""
import ast
from datetime import datetime, timezone

import pytest
from _load import ROOT, load

boost_link = load("utils/boost_link.py", "boost_link_under_test")


@pytest.mark.parametrize("url", [
    "https://t.me/boost/restojobs",
    "http://t.me/boost/restojobs/",
    "t.me/boost/RestoJobs",
    "https://telegram.me/boost/restojobs",
])
def test_public_boost_link(url):
    assert boost_link.boost_target(url).lower() == "@restojobs"


def test_private_boost_link():
    assert boost_link.boost_target("https://t.me/boost?c=1234567890") == -1001234567890


@pytest.mark.parametrize("url", [
    "", None, "https://t.me/restojobs", "https://example.com/boost/restojobs",
    "https://t.me/boost/", "https://t.me/boost/ab", "https://t.me/boost?c=abc",
])
def test_not_a_boost_link(url):
    # За такие ссылки бот не узнает, кто проголосовал, — и не платит
    assert boost_link.boost_target(url) is None


def test_is_boost_chat():
    assert boost_link.is_boost_chat("@restojobs", -100111, "RestoJobs")
    assert not boost_link.is_boost_chat("@restojobs", -100111, "other")
    assert not boost_link.is_boost_chat("@restojobs", -100111, None)
    assert boost_link.is_boost_chat(-100111, -100111, None)
    assert not boost_link.is_boost_chat(-100111, -100222, "restojobs")
    assert not boost_link.is_boost_chat(None, -100111, "restojobs")


def test_renamed_channel_is_recognized_by_known_id():
    # Юзернейм канала сменился, но ID запомнен — событие всё равно засчитывается
    assert boost_link.is_boost_chat("@restojobs", -100111, "new_name", known_chat_id=-100111)
    assert not boost_link.is_boost_chat("@restojobs", -100222, "new_name", known_chat_id=-100111)
    assert not boost_link.is_boost_chat(None, -100111, None, known_chat_id=-100111)


def test_natural_expiry():
    expires = 1_800_000_000
    tolerance = boost_link.EXPIRY_TOLERANCE_SECONDS
    # Убран в момент истечения (или около него) — истёк сам
    assert boost_link.is_natural_expiry(expires, expires)
    assert boost_link.is_natural_expiry(expires - tolerance + 1, expires)
    assert boost_link.is_natural_expiry(expires + 60, expires)
    # Убран заранее — отменён человеком
    assert not boost_link.is_natural_expiry(expires - 86_400, expires)
    # Нет данных — считаем отменой
    assert not boost_link.is_natural_expiry(None, expires)
    assert not boost_link.is_natural_expiry(expires, None)


def test_to_ts():
    assert boost_link.to_ts(None) is None
    assert boost_link.to_ts(datetime(2026, 1, 1, tzinfo=timezone.utc)) == 1_767_225_600


def test_boost_settings_defaults_and_values():
    settings = load("data/settings.py", "settings_boost_under_test")
    settings.replace_cache({})
    s = settings.get_settings()
    assert s.boost_credit == 0  # по умолчанию не платим
    assert s.boost_hold_hours == 72
    assert s.boost_chat_id is None
    settings.replace_cache({"boost_credit": "700", "boost_hold_hours": 0, "boost_chat_id": -1009876543210})
    s = settings.get_settings()
    assert s.boost_credit == 700
    assert s.boost_hold_hours == 0
    assert s.boost_chat_id == -1009876543210
    assert {"boost_credit", "boost_hold_hours", "boost_chat_id"} <= settings.SETTING_KEYS
    # Голос в опросе внутри бота больше не оплачивается
    assert "vote_credit" not in settings.SETTING_KEYS
    assert not hasattr(s, "vote_credit")


def test_in_bot_poll_is_not_paid():
    src = (ROOT / "handlers" / "private" / "fallback.py").read_text(encoding="utf-8")
    names = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert "reward_vote_once" not in names
    assert "vote_credit" not in names


def test_boost_updates_are_requested():
    # Без этих типов в allowed_updates Telegram не присылает события бустов
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"chat_boost"' in src
    assert '"removed_chat_boost"' in src
    assert "create_table_boost_rewards" in src


def test_boost_reversals_reported_by_reversal_date():
    # Снятия за отмену голоса считаются по дате отмены, а не по дате начисления
    src = (ROOT / "handlers" / "private" / "admin" / "finance_reports.py").read_text(encoding="utf-8")
    assert "reversed_at >= {since}" in src
