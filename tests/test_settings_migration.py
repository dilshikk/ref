"""Тесты разового переноса url.yml и .env в bot_settings (#114, #116). Запуск: python -m pytest tests"""
import sys
import types

import pytest
from _load import load


@pytest.fixture(scope="module")
def mig():
    # utils/settings_migration.py импортирует data.settings. Пакет data тянет .env,
    # поэтому подставляем лёгкий пакет с одним модулем settings.
    saved = {k: sys.modules.get(k) for k in ("data", "data.settings")}
    pkg = types.ModuleType("data")
    pkg.__path__ = []
    sys.modules["data"] = pkg
    sys.modules["data.settings"] = load("data/settings.py", "data.settings")
    try:
        yield load("utils/settings_migration.py", "settings_migration_under_test")
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module


YAML = {
    "invited_credit": 500,
    "min_balance": 50000,
    "subcr_channel": "https://t.me/group",
    "subcr_chat_id": "-100111",
    "channels": ["@news", {"chat_id": "-100222", "link": "https://t.me/+abc"}, ""],
    "unknown_key": 1,
    "support": None,
}
ENV = {"GROUP_ID": "-100999", "LOG_GROUP_ID": "-100888", "MONEY_ADMIN": "5", "MONEY_ADMINS": "7, 5 x"}


def test_empty_database_takes_yaml_and_env(mig):
    plan = mig.plan_migration({}, YAML, ENV)
    assert plan.values["invited_credit"] == 500
    assert plan.values["subcr_channel"] == "https://t.me/group"
    assert plan.values["group_id"] == -100999
    assert plan.values["log_group_id"] == -100888
    assert plan.values["money_admins"] == [5, 7]
    assert plan.sources["invited_credit"] == "url.yml"
    assert plan.sources["group_id"] == ".env"
    # subcr_chat_id устарел, неизвестные ключи и пустые значения не переносятся
    assert "subcr_chat_id" not in plan.values
    assert "unknown_key" not in plan.values
    assert "support" not in plan.values
    assert set(plan.skipped) == {"subcr_chat_id", "unknown_key", "support"}
    assert plan.channels == [("@news", "https://t.me/news"), ("-100222", "https://t.me/+abc")]


def test_yaml_is_ignored_when_database_has_settings(mig):
    plan = mig.plan_migration({"invited_credit": 700}, YAML, {})
    assert plan.values == {}
    assert plan.channels == []
    assert plan.is_empty


def test_env_does_not_overwrite_values_saved_in_admin_panel(mig):
    existing = {"group_id": -100555, "money_admins": [1]}
    plan = mig.plan_migration(existing, None, ENV)
    assert "group_id" not in plan.values
    assert "money_admins" not in plan.values
    assert plan.values == {"log_group_id": -100888}


def test_second_run_changes_nothing(mig):
    first = mig.plan_migration({}, YAML, ENV)
    second = mig.plan_migration(dict(first.values), YAML, ENV)
    assert second.is_empty


def test_bad_env_values_are_skipped(mig):
    plan = mig.plan_migration({}, None, {"GROUP_ID": "abc", "LOG_GROUP_ID": "", "MONEY_ADMINS": "-1, x"})
    assert plan.is_empty
