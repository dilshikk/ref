"""#106: список «👥 Мои приглашённые». Запуск: python -m pytest tests"""
from datetime import datetime

from _load import load

invited_list = load("utils/invited_list.py", "invited_list_under_test")


def test_active_and_reversed_rows():
    lines = invited_list.format_invited_lines([
        {"full_name": "Ali", "invited_at": datetime(2026, 10, 29, 12, 0), "reversed_at": None},
        {"full_name": "Vali", "invited_at": datetime(2026, 10, 1), "reversed_at": datetime(2026, 10, 2)},
    ])
    assert lines == [
        "1. Ali — 29.10.2026",
        "2. Vali — 01.10.2026 — ↩️ вышел, начисление снято",
    ]


def test_name_is_escaped_and_missing_name_has_fallback():
    # Имя с < > & раньше ломало HTML (#50, #104)
    lines = invited_list.format_invited_lines([
        {"full_name": "<b>&</b>", "invited_at": None, "reversed_at": None},
        {"full_name": None, "invited_at": None, "reversed_at": None},
    ])
    assert lines[0] == "1. &lt;b&gt;&amp;&lt;/b&gt; — —"
    assert lines[1] == "2. Участник без имени — —"


def test_long_name_is_shortened():
    line = invited_list.format_invited_lines([{"full_name": "x" * 100, "invited_at": None}])[0]
    assert len(line) < 60
    assert "…" in line
