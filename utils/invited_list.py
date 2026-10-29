"""Строки списка «👥 Мои приглашённые» (#106).
Отдельный модуль без зависимостей от бота — чтобы проверять тестом."""
import html
from datetime import datetime
from typing import Any, Mapping, Sequence

INVITED_LIST_LIMIT = 20
_MAX_NAME_LEN = 40


def _name(row: Mapping[str, Any]) -> str:
    raw = str(row.get("full_name") or "").strip()
    if not raw:
        # Приглашённый мог вступить в группу, ни разу не открыв бота
        return "Участник без имени"
    if len(raw) > _MAX_NAME_LEN:
        raw = raw[: _MAX_NAME_LEN - 1] + "…"
    return html.escape(raw)


def _date(value: Any) -> str:
    return value.strftime("%d.%m.%Y") if isinstance(value, datetime) else "—"


def format_invited_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """«1. Имя — 29.10.2026», у вышедших — пометка, что начисление снято."""
    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        line = f"{index}. {_name(row)} — {_date(row.get('invited_at'))}"
        if row.get("reversed_at") is not None:
            line += " — ↩️ вышел, начисление снято"
        lines.append(line)
    return lines
