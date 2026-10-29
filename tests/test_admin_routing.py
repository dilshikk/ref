"""Структура админ-панели после разбиения (#37). Только разбор исходников (ast),
без импорта aiogram и настроек бота. Запуск: python -m pytest tests"""
import ast
import re
from collections import Counter

from _load import ROOT

ADMIN_DIR = ROOT / "handlers" / "private" / "admin"
SECTIONS = ["menu", "stats", "finance", "users", "channels", "bot", "broadcasts", "settings", "security", "logs"]


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
            texts.update(re.findall(r'"([^"]+)"', first))
    assert [t for t, c in texts.items() if c > 1] == []


def test_settings_are_saved_atomically():
    # #17: только utils.yaml_io.save_url_yaml, никаких open("w") по url.yml
    for path in ADMIN_DIR.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "open(" not in src, path.name
        assert "_save_yaml" not in src, path.name


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
