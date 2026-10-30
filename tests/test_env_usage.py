"""#117: бот читает из окружения только BOT_TOKEN, ADMINS, DB_*, PAYOUT_ENCRYPTION_KEY
и ALLOW_PLAINTEXT_PAYOUTS. Всё остальное — в bot_settings и меняется в админке.
Только разбор исходников (ast), без импорта бота. Запуск: python -m pytest tests"""
import ast

from _load import ROOT

ALLOWED = {
    "BOT_TOKEN", "ADMINS",
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASS", "DB_NAME",
    "PAYOUT_ENCRYPTION_KEY", "ALLOW_PLAINTEXT_PAYOUTS",
}
# Разовый перенос старых настроек читает прежние переменные — только он (#114, #116)
MIGRATION_FILES = {"services/settings_store.py", "utils/settings_migration.py"}
_SKIP_DIRS = {"tests", ".venv", "venv", ".git"}


def _sources():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if _SKIP_DIRS & set(rel.parts):
            continue
        yield rel.as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _first_str(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _env_reads(tree: ast.AST):
    """(имя переменной или None, если не строка) для env.*(...), os.getenv(...), os.environ.get/[...]."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            # env.str("X"), env.int("X"), env.list("X") …
            if isinstance(owner, ast.Name) and owner.id == "env" and node.func.attr != "read_env":
                yield _first_str(node)
            # os.getenv("X")
            if isinstance(owner, ast.Name) and owner.id == "os" and node.func.attr == "getenv":
                yield _first_str(node)
            # os.environ.get("X")
            if (isinstance(owner, ast.Attribute) and owner.attr == "environ"
                    and node.func.attr in {"get", "setdefault", "pop"}):
                yield _first_str(node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
            key = node.slice
            yield key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None


def test_env_reads_are_found():
    # Защита от «пустой» проверки: если разбор сломается, тест ниже прошёл бы впустую
    reads = {name for rel, tree in _sources() if rel in {"data/config.py", "loader.py"} for name in _env_reads(tree)}
    assert {"BOT_TOKEN", "ADMINS", "DB_HOST"} <= reads


def test_only_allowed_env_variables_are_read():
    bad = [
        f"{rel}: {name}"
        for rel, tree in _sources()
        if rel not in MIGRATION_FILES
        for name in _env_reads(tree)
        if name not in ALLOWED
    ]
    assert bad == []


def test_env_example_has_only_allowed_variables():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    names = {line.split("=", 1)[0].lstrip("# ").strip() for line in lines if "=" in line and not line.startswith("# python")}
    names = {n for n in names if n.isupper() and " " not in n}
    assert names <= ALLOWED, sorted(names - ALLOWED)
    assert {"BOT_TOKEN", "ADMINS", "DB_HOST", "DB_NAME", "PAYOUT_ENCRYPTION_KEY"} <= names


def test_docker_compose_mounts_no_config():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./config" not in compose
    assert "URL_YML_PATH" not in compose
    assert not (ROOT / "data" / "url.yml.example").exists()
