"""
Защита от возврата устаревшего кода в SafeDatabase (#49). Запуск: python -m pytest tests

Модуль не импортируем (он тянет SQLAlchemy и настройки бота) — разбираем исходник
через ast: так проверка работает без базы и без .env.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAFE_DB = ROOT / "utils" / "db" / "safe_db.py"
REFERRAL_DB = ROOT / "utils" / "db" / "referral_db.py"

# Админ-экраны, которые раньше показывали сырые url_yaml.get(...) вместо реальных значений
ADMIN_SCREENS = [
    ROOT / "handlers" / "private" / "admin" / "finance.py",
    ROOT / "handlers" / "private" / "admin" / "settings.py",
    ROOT / "handlers" / "private" / "admin" / "security.py",
    ROOT / "handlers" / "private" / "admin" / "bot.py",
]
# Числовые настройки: их значение должно браться только из get_settings()
NUMERIC_KEYS = {
    "invited_credit", "vote_credit", "min_balance", "max_withdraw",
    "group_member_count", "antispam_referral_limit",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"класс {name} не найден в {path}")


def _methods(cls: ast.ClassDef) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_only_checked_approve_payout_exists():
    # Старая версия без проверки баланса не должна вернуться в базовый класс
    assert "approve_payout_if_pending" not in _methods(_class(SAFE_DB, "SafeDatabase"))
    approve = _methods(_class(REFERRAL_DB, "ReferralDatabase")).get("approve_payout_if_pending")
    assert approve is not None
    assert "FOR UPDATE" in ast.get_source_segment(REFERRAL_DB.read_text(encoding="utf-8"), approve)


def test_safe_db_does_not_read_url_yaml():
    tree = ast.parse(SAFE_DB.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "url_yaml" not in names


def test_add_balance_requires_price():
    add_balance = _methods(_class(SAFE_DB, "SafeDatabase"))["add_balance"]
    kwonly = {a.arg: d for a, d in zip(add_balance.args.kwonlyargs, add_balance.args.kw_defaults)}
    positional = [a.arg for a in add_balance.args.args]
    assert "price" not in positional, "price должен передаваться только по имени"
    assert "price" in kwonly and kwonly["price"] is None, "у price не должно быть значения по умолчанию"


def _url_yaml_get_keys(path: Path) -> set[str]:
    """Ключи из вызовов url_yaml.get("ключ", ...) в файле."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "url_yaml"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(str(node.args[0].value))
    return keys


def _get_calls_with_default(path: Path) -> set[str]:
    """Ключи, для которых url_yaml.get вызывается со своим умолчанием — признак показа значения."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "url_yaml"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(str(node.args[0].value))
    return keys


def test_admin_screens_show_numbers_from_get_settings():
    # url_yaml.get("ключ") без умолчания допустим — это old_value для аудита.
    # Со своим умолчанием — это показ админу, и он расходился с тем, что делает бот.
    for path in ADMIN_SCREENS:
        bad = _get_calls_with_default(path) & NUMERIC_KEYS
        assert not bad, f"{path.name}: числовые настройки читаются в обход get_settings(): {sorted(bad)}"
