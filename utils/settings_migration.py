"""
Разовый перенос старых настроек в таблицу bot_settings (#114, #116).

Чистая функция без базы и файлов, чтобы её можно было проверить тестом:
- url.yml переносится целиком, только если в базе ещё нет ни одной настройки;
- group_id, log_group_id и money_admins берутся из старых переменных .env
  (GROUP_ID, LOG_GROUP_ID, MONEY_ADMIN, MONEY_ADMINS), только если их ещё нет в базе;
- каналы из url.yml (channels) добавляются в таблицу channel;
- subcr_chat_id не переносится: участники давно считаются по одной группе (group_id).

Повторный запуск с уже перенесёнными настройками ничего не меняет.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from data.settings import SETTING_KEYS

# Ключи url.yml, которые не переносятся как настройки
_SKIPPED_YAML_KEYS = frozenset({"channels", "subcr_chat_id"})


@dataclass
class MigrationPlan:
    values: dict[str, Any] = field(default_factory=dict)   # что записать в bot_settings
    sources: dict[str, str] = field(default_factory=dict)  # откуда взят каждый ключ
    channels: list[tuple[str, str]] = field(default_factory=list)  # (chat_id, link) для таблицы channel
    skipped: list[str] = field(default_factory=list)       # ключи url.yml, которые не перенесены

    @property
    def is_empty(self) -> bool:
        return not self.values and not self.channels


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = str(env.get(name) or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _env_ids(env: Mapping[str, str], *names: str) -> list[int]:
    ids: set[int] = set()
    for name in names:
        for part in str(env.get(name) or "").replace(",", " ").split():
            if part.isdigit() and int(part) > 0:
                ids.add(int(part))
    return sorted(ids)


def _channel(item: Any) -> tuple[str, str] | None:
    """'@name' → ('@name', 'https://t.me/name'); {'chat_id': ..., 'link': ...} → как есть."""
    if isinstance(item, Mapping):
        chat_id = str(item.get("chat_id") or "").strip()
        link = str(item.get("link") or "").strip()
    else:
        chat_id = str(item or "").strip()
        link = ""
    if not chat_id:
        return None
    if not link and chat_id.startswith("@") and len(chat_id) > 1:
        link = f"https://t.me/{chat_id[1:]}"
    return chat_id, link


def plan_migration(
    existing: Mapping[str, Any],
    yaml_data: Mapping[str, Any] | None,
    env: Mapping[str, str],
) -> MigrationPlan:
    plan = MigrationPlan()

    if not existing and yaml_data:
        for key, value in yaml_data.items():
            if key in _SKIPPED_YAML_KEYS or key not in SETTING_KEYS or value is None:
                if key != "channels":
                    plan.skipped.append(str(key))
                continue
            plan.values[key] = value
            plan.sources[key] = "url.yml"
        for item in yaml_data.get("channels") or []:
            channel = _channel(item)
            if channel is not None:
                plan.channels.append(channel)

    def take_env(key: str, value: Any) -> None:
        if key in existing or key in plan.values or value in (None, []):
            return
        plan.values[key] = value
        plan.sources[key] = ".env"

    take_env("group_id", _env_int(env, "GROUP_ID"))
    take_env("log_group_id", _env_int(env, "LOG_GROUP_ID"))
    take_env("money_admins", _env_ids(env, "MONEY_ADMIN", "MONEY_ADMINS"))
    return plan
