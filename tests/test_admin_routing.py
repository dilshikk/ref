"""Структура админ-панели после разбиения (#37). Только разбор исходников (ast),
без импорта aiogram и настроек бота. Запуск: python -m pytest tests"""
import ast
import re
from collections import Counter

from _load import ROOT

ADMIN_DIR = ROOT / "handlers" / "private" / "admin"
# finance_reports (#68, #71, #72) — сразу после finance: «💳 Балансы», «📊 Отчёты», «🎁 Бонусы».
# security убран: его настройки повторяли «⚙️ Настройки» и «👥 Пользователи».
SECTIONS = [
    "menu", "stats", "finance", "finance_reports", "users", "channels",
    "bot", "broadcasts", "settings", "logs",
]


def _handlers():
    """(модуль, имя функции, объект роутера, тип события, первый фильтр) для каждого обработчика."""
    out = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    target = dec.func.value
                    if isinstance(target, ast.Name) and target.id in {"router", "forms_router"}:
                        first = ast.unparse(dec.args[0]) if dec.args else ""
                        out.append((path.stem, node.name, target.id, dec.func.attr, first))
    return out


def test_old_monolith_is_gone():
    assert not (ROOT / "handlers" / "private" / "admin_panel.py").exists()


def test_every_section_is_included_and_forms_router_is_last():
    init = (ADMIN_DIR / "__init__.py").read_text(encoding="utf-8")
    included = re.findall(r"^\s+(\w+)(?:\.router)?,$", init.split("include_routers(", 1)[1], flags=re.M)
    assert included == SECTIONS + ["forms_router"]


def test_form_inputs_live_only_in_forms_router():
    # Кнопка меню посреди формы должна открывать раздел, а не уходить в форму как значение
    for module, name, router, event, first in _handlers():
        is_form = event == "message" and first.startswith("AdminFlow.")
        assert is_form == (router == "forms_router"), f"{module}.{name}"


def test_handler_names_are_unique():
    names = Counter(name for _, name, *_ in _handlers())
    assert [n for n, c in names.items() if c > 1] == []


def test_button_texts_are_not_duplicated_between_sections():
    texts = Counter()
    for _, _, _, event, first in _handlers():
        if event == "message":
            texts.update(re.findall(r"['\"]([^'\"]+)['\"]", first))
    assert [t for t, c in texts.items() if c > 1] == []


def test_settings_are_saved_to_database():
    # #115: настройки сохраняются только через set_setting() в bot_settings — файлов больше нет
    assert not (ROOT / "utils" / "yaml_io.py").exists()
    for path in ADMIN_DIR.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "open(" not in src, path.name
        assert "_save_yaml" not in src, path.name
        assert "save_url_yaml" not in src, path.name


# Имена, которые убраны из data/config.py (#113–#116). Импорт любого из них
# ронял бы бот при запуске (ImportError), а ast-тесты этого не замечали.
_REMOVED_CONFIG_NAMES = {"url_yaml", "URL_YML_PATH", "GROUP_ID", "LOG_GROUP_ID", "MONEY_ADMIN", "MONEY_ADMINS"}
_CONFIG_EXPORTS = {"env", "BOT_TOKEN", "ADMINS"}


def test_only_existing_names_are_imported_from_config():
    config = ast.parse((ROOT / "data" / "config.py").read_text(encoding="utf-8"))
    defined = {
        t.id
        for n in config.body
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Name)
    }
    assert _CONFIG_EXPORTS <= defined
    assert not (_REMOVED_CONFIG_NAMES & defined)

    for path in ROOT.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "data.config":
                missing = {a.name for a in node.names} - defined
                assert not missing, f"{path.relative_to(ROOT)}: {sorted(missing)}"
            if isinstance(node, ast.ImportFrom) and node.module == "utils.yaml_io":
                raise AssertionError(f"{path.relative_to(ROOT)}: utils.yaml_io удалён")


def test_relative_imports_resolve():
    # После переноса в подпакет «from .x» внутри функций указывал бы не туда
    for path in ADMIN_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = ADMIN_DIR if node.level == 1 else ADMIN_DIR.parents[node.level - 2]
                if node.module:
                    target = base.joinpath(*node.module.split("."))
                    assert target.with_suffix(".py").exists() or target.is_dir(), f"{path.name}: {node.module}"
                else:
                    for alias in node.names:
                        assert (base / f"{alias.name}.py").exists(), f"{path.name}: {alias.name}"
