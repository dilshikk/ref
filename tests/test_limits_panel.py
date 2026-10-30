"""#92: «📊 Лимиты» — инлайн-панель изменения, дубль «📱 Ограничения» удалён.
Только разбор исходников (ast). Запуск: python -m pytest tests"""
import ast

from _load import ROOT

SETTINGS_HANDLERS = ROOT / "handlers" / "private" / "admin" / "settings.py"
REQUIRED = {"antispam_referral_daily_limit", "referral_hold_hours", "add_min_hours", "add_min_score"}


def _limit_keys() -> set[str]:
    for node in ast.parse(SETTINGS_HANDLERS.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_LIMIT_FIELDS"
            and isinstance(node.value, ast.Dict)
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("_LIMIT_FIELDS не найден")


def _setting_keys() -> set[str]:
    tree = ast.parse((ROOT / "data" / "settings.py").read_text(encoding="utf-8"))
    return {
        n.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "SETTING_KEYS"
        for n in ast.walk(node.value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_limits_panel_edits_every_limit():
    # Раньше эти лимиты менялись только правкой url.yml на сервере
    assert REQUIRED <= _limit_keys()


def test_limits_panel_keys_are_known_settings():
    # Неизвестный ключ set_setting() не сохранит (KeyError) — ловим заранее
    known = _setting_keys()
    assert known, "SETTING_KEYS не разобран"
    assert _limit_keys() <= known


def test_restrictions_duplicate_is_gone():
    for path in (ROOT / "keyboards").rglob("*.py"):
        assert "📱 Ограничения" not in path.read_text(encoding="utf-8"), path.name
