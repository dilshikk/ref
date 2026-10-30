"""Регрессия: «🔄 Обновить меню» падала с TypeError — set_all_commands
вызывали без admin_ids. Проверяем, что каждый вызов передаёт список админов."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".venv", "tests", ".git"}


def _python_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
    ]


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_set_all_commands_always_gets_admin_ids() -> None:
    calls_found = 0
    bad_calls: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "set_all_commands":
                continue
            calls_found += 1
            has_admin_ids = len(node.args) >= 2 or any(
                keyword.arg == "admin_ids" for keyword in node.keywords
            )
            if not has_admin_ids:
                bad_calls.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert calls_found >= 2, "ожидались вызовы при запуске и в «🔄 Обновить меню»"
    assert not bad_calls, f"set_all_commands вызван без admin_ids: {bad_calls}"
