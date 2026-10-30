"""Тесты чтения настроек (#22, #18, #88, #113). Запуск: python -m pytest tests"""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.py"


@pytest.fixture
def load():
    """Грузит data/settings.py отдельной копией и заполняет кэш настроек так,
    как это делает load_settings() после чтения таблицы bot_settings."""
    def _load(values: dict):
        spec = importlib.util.spec_from_file_location("settings_under_test", _PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.replace_cache(values)
        return module
    return _load


def test_garbage_numbers_fall_back_to_defaults(load):
    # Именно такие значения раньше роняли int() в /start
    s = load({"invited_credit": "abc", "min_balance": "", "max_withdraw": None,
              "boost_credit": True, "group_member_count": [1]}).get_settings()
    assert s.invited_credit == 0
    assert s.min_balance == 0
    assert s.max_withdraw == 9_999_999
    assert s.boost_credit == 0
    assert s.group_member_count == 3


def test_numbers_saved_as_strings_are_accepted(load):
    s = load({"invited_credit": " 500 ", "min_balance": "10000"}).get_settings()
    assert s.invited_credit == 500
    assert s.min_balance == 10000


def test_values_below_minimum_use_default(load):
    s = load({"max_withdraw": 0, "antispam_referral_limit": -5, "min_balance": -1}).get_settings()
    assert s.max_withdraw == 9_999_999
    assert s.antispam_referral_limit == 20
    assert s.min_balance == 0


def test_antifraud_defaults(load):
    # #18: лимит в час снижен со 100 до 20, добавлен суточный
    s = load({}).get_settings()
    assert s.antispam_referral_limit == 20
    assert s.antispam_referral_daily_limit == 100
    assert s.add_min_hours == 24
    assert s.add_min_score == 2


def test_antifraud_values(load):
    s = load({"antispam_referral_daily_limit": "50", "add_min_hours": 0, "add_min_score": 0}).get_settings()
    assert s.antispam_referral_daily_limit == 50
    assert s.add_min_hours == 0
    assert s.add_min_score == 0
    assert load({"antispam_referral_daily_limit": 0}).get_settings().antispam_referral_daily_limit == 100
    assert load({"add_min_hours": "x"}).get_settings().add_min_hours == 24


def test_zero_hold_hours_is_allowed(load):
    assert load({"referral_hold_hours": 0}).get_settings().referral_hold_hours == 0
    assert load({"boost_hold_hours": 0}).get_settings().boost_hold_hours == 0


def test_changes_apply_without_restart(load):
    module = load({"invited_credit": 100})
    assert module.get_settings().invited_credit == 100
    module.cache_set("invited_credit", 250)  # так пишет set_setting() из админки
    assert module.get_settings().invited_credit == 250


def test_poll_options(load):
    assert load({"poll_options": "Да | Нет | "}).get_settings().poll_options == ("Да", "Нет")
    assert load({"poll_options": ["одна"]}).get_settings().poll_options == (
        "Отлично", "Нормально", "Нужно улучшить")
    assert len(load({"poll_options": [str(i) for i in range(15)]}).get_settings().poll_options) == 10
    assert load({"poll_question": "  "}).get_settings().poll_question == "Насколько вам удобен наш бот?"


def test_text_templates_only_known_nonempty_keys(load):
    # #88: пустой шаблон и неизвестный ключ не заменяют текст по умолчанию
    s = load({"text_templates": {"start": " Привет ", "help": "  ", "other": "x", "support": None}}).get_settings()
    assert s.text_templates == {"start": "Привет"}
    assert load({"text_templates": "мусор"}).get_settings().text_templates == {}
    assert load({}).get_settings().text_templates == {}


def test_custom_text_is_escaped_and_fills_name(load):
    module = load({"text_templates": {"start": "Привет, {name}! <b>500</b> & больше"}})
    assert module.custom_text("start", "<Ali>") == (
        "Привет, &lt;Ali&gt;! &lt;b&gt;500&lt;/b&gt; &amp; больше"
    )
    assert module.custom_text("help") is None


def test_custom_text_changes_apply_without_restart(load):
    module = load({})
    assert module.custom_text("support") is None
    module.cache_set("text_templates", {"support": "Пишите нам"})  # так пишет «📝 Тексты»
    assert module.custom_text("support") == "Пишите нам"


def test_group_and_money_admins(load):
    # #116: группа и денежные админы теперь тоже в bot_settings
    s = load({"group_id": "-1001234567890", "log_group_id": "", "money_admins": [222, "111", 0, "x"]}).get_settings()
    assert s.group_id == -1001234567890
    assert s.log_group_id is None
    assert s.money_admins == (111, 222)
    assert load({}).get_settings().money_admins == ()
