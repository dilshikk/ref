"""Неизвестные команды и непонятные сообщения бот игнорирует: никаких ответов.
Только разбор исходника (ast), без импорта aiogram и настроек бота."""
import ast

from _load import ROOT

FALLBACK = ROOT / "handlers" / "private" / "fallback.py"
SILENT_HANDLERS = {"unknown_command", "fallback_handler"}
REPLY_METHODS = {"answer", "reply", "send_message", "answer_poll", "answer_photo"}


def _functions() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(FALLBACK.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}


def test_silent_handlers_exist():
    assert SILENT_HANDLERS <= set(_functions())


def test_silent_handlers_do_not_reply():
    funcs = _functions()
    for name in SILENT_HANDLERS:
        calls = [
            n.func.attr
            for n in ast.walk(funcs[name])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        assert not REPLY_METHODS & set(calls), f"{name} не должен отвечать пользователю"
