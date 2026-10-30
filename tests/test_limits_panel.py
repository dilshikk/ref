"""Каждая числовая настройка меняется ровно в одной панели «⚙️ Настройки».

Раньше лимит рефералов в час редактировался в трёх местах («📊 Лимиты»,
«🎁 Реферальные настройки», «🔒 Безопасность»), сумма за приглашение — в двух.
Только разбор исходников (ast). Запуск: python -m pytest tests"""
import ast

from _load import ROOT

ADMIN_DIR = ROOT / "handlers" / "private" / "admin"
SETTINGS_HANDLERS = ADMIN_DIR / "settings.py"
NUMERIC_KEYS = {
    "min_balance", "max_withdraw", "group_member_count",
    "invited_credit", "boost_credit", "boost_hold_hours",
    "antispam_referral_limit", "antispam_referral_daily_limit",
    "referral_hold_hours", "add_min_hours", "add_min_score",
}
# Старые панели-дубли: их не должно быть нигде в админке
REMOVED_PANELS = {"_LIMIT_FIELDS", "_REFERRAL_FIELDS", "_TARIFF_FIELDS"}


def _module_dict(name: str) -> ast.Dict:
    for node in ast.parse(SETTINGS_HANDLERS.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.value, ast.Dict)
        ):
            return node.value
    raise AssertionError(f"{name} не найден")


def _fields() -> dict[str, str]:
    """{ключ настройки: панель}."""
    out: dict[str, str] = {}
    fields = _module_dict("_FIELDS")
    for key, value in zip(fields.keys, fields.values):
        assert isinstance(key, ast.Constant) and isinstance(value, ast.Dict)
        props = {k.value: v for k, v in zip(value.keys, value.values) if isinstance(k, ast.Constant)}
        panel = props["panel"]
        assert isinstance(panel, ast.Constant)
        out[key.value] = panel.value
    return out


def _const_keys(name: str) -> set[str]:
    return {k.value for k in _module_dict(name).keys if isinstance(k, ast.Constant)}


def _const_values(name: str) -> set[str]:
    return {v.value for v in _module_dict(name).values if isinstance(v, ast.Constant)}


def _setting_keys() -> set[str]:
    tree = ast.parse((ROOT / "data" / "settings.py").read_text(encoding="utf-8"))
    return {
        n.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "SETTING_KEYS"
        for n in ast.walk(node.value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_every_number_has_a_panel():
    # Раньше часть лимитов менялась только правкой url.yml на сервере (#92)
    assert NUMERIC_KEYS <= set(_fields())


def test_panel_keys_are_known_settings():
    # Неизвестный ключ set_setting() не сохранит (KeyError) — ловим заранее
    known = _setting_keys()
    assert known, "SETTING_KEYS не разобран"
    assert set(_fields()) <= known


def test_in_bot_poll_reward_is_gone():
    # Голос в опросе внутри бота больше не оплачивается
    assert "vote_credit" not in _fields()
    assert "vote_credit" not in _setting_keys()


def test_every_panel_is_described_and_has_button():
    panels = set(_fields().values())
    assert panels == _const_keys("_PANELS")
    assert panels == _const_values("_PANEL_BY_BUTTON")


def _calls_with_numeric_key(path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name not in {"set_setting", "_save"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value in NUMERIC_KEYS:
                found.append(f"{path.name}:{node.lineno} {arg.value}")
    return found


def test_numbers_are_saved_only_from_settings_panel():
    # Второе место, где сохраняется то же число, снова сделало бы меню запутанным
    found = [hit for path in sorted(ADMIN_DIR.glob("*.py")) for hit in _calls_with_numeric_key(path)]
    assert found == []


def test_old_duplicate_panels_are_gone():
    for path in ADMIN_DIR.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for name in REMOVED_PANELS:
            assert name not in src, f"{path.name}: {name}"
    assert not (ADMIN_DIR / "security.py").exists()


def test_restrictions_duplicate_is_gone():
    for path in (ROOT / "keyboards").rglob("*.py"):
        assert "📱 Ограничения" not in path.read_text(encoding="utf-8"), path.name
