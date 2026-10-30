"""#68, #71, #72: «💳 Балансы», «📊 Отчёты», «🎁 Бонусы» в «💰 Финансы».
Чистые функции грузятся по пути (без aiogram и настроек). Запуск: python -m pytest tests"""
import ast
from decimal import Decimal

from _load import ROOT, load

ft = load("utils/finance_text.py", "finance_text_under_test")


def test_money_formats_thousands_and_garbage():
    assert ft.money(0) == "0"
    assert ft.money(1234567) == "1 234 567"
    # NUMERIC(10, 2) из PayoutRequests.amount приходит как Decimal
    assert ft.money(Decimal("15000.00")) == "15 000"
    assert ft.money(None) == "0"
    assert ft.money("abc") == "0"
    assert ft.money(-500) == "-500"


def test_parse_period_accepts_only_known_periods():
    assert ft.parse_period("fin_report:1") == 1
    assert ft.parse_period("fin_report:7") == 7
    assert ft.parse_period("fin_report:30") == 30
    # Поддельный callback не должен строить отчёт за произвольный срок
    for bad in (None, "", "fin_report:", "fin_report:365", "fin_report:-1", "fin_report:7x", "other:7"):
        assert ft.parse_period(bad) is None, bad


def test_default_period_is_known():
    assert ft.DEFAULT_REPORT_DAYS in ft.REPORT_PERIODS


def test_user_label_escapes_html():
    # #50: имя с «<» или «&» ломало HTML, и Telegram отклонял всё сообщение
    label = ft.user_label("<b>Ann & Co", "a<b", "123")
    assert "<b>Ann" not in label
    assert "&lt;b&gt;Ann &amp; Co" in label
    assert "@a&lt;b" in label
    assert label.endswith("/uid123")
    assert ft.user_label(None, None, 5) == "— (—) — /uid5"


def _finance_kb_buttons() -> set[str]:
    tree = ast.parse((ROOT / "keyboards" / "reply_keyboards" / "admin_menu.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "finance_kb":
            return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    raise AssertionError("finance_kb не найдена")


def test_finance_menu_shows_new_buttons():
    assert {"💳 Балансы", "📊 Отчёты", "🎁 Бонусы"} <= _finance_kb_buttons()


def test_finance_reports_router_is_included():
    src = (ROOT / "handlers" / "private" / "admin" / "__init__.py").read_text(encoding="utf-8")
    assert "finance_reports.router" in src
