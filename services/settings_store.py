"""
Чтение и запись настроек бота (#113–#116).

- load_settings() — при старте читает bot_settings в память (data/settings.py);
- set_setting() — сохраняет значение в базу и сразу обновляет копию в памяти;
- migrate_legacy_settings() — один раз переносит старые url.yml и переменные .env.

Админка сохраняет настройки только через set_setting(): записи в файлы больше нет.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from data.config import ADMINS
from data.settings import SETTING_KEYS, cache_set, get_raw, get_settings, replace_cache
from loader import db
from utils.settings_migration import plan_migration

_ROOT = Path(__file__).resolve().parent.parent
# Где искала url.yml старая версия бота. Используется только для разового переноса.
_LEGACY_YAML_PATHS = (_ROOT / "config" / "url.yml", _ROOT / "data" / "url.yml")

GROUP_NOT_SET_TEXT = (
    "⚠️ ID группы не задан. Укажите его в «📢 Каналы и группы» → «👥 Группа приглашений» → «🆔 ID группы»."
)


def _legacy_yaml_path() -> Path | None:
    custom = os.environ.get("URL_YML_PATH", "").strip()
    candidates = (Path(custom),) if custom else _LEGACY_YAML_PATHS
    return next((p for p in candidates if p.is_file()), None)


def _read_legacy_yaml() -> dict[str, Any] | None:
    path = _legacy_yaml_path()
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Не удалось прочитать старый {path} для переноса настроек: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"{path} пустой или повреждён — переносить нечего")
        return None
    logger.info(f"Найден старый файл настроек {path}")
    return data


async def migrate_legacy_settings() -> None:
    yaml_data = _read_legacy_yaml()
    env = dict(os.environ)
    plan = await db.run_settings_migration(lambda existing: plan_migration(existing, yaml_data, env))
    if plan.skipped:
        logger.info(f"Из url.yml не перенесены устаревшие ключи: {sorted(plan.skipped)}")


async def load_settings() -> None:
    replace_cache(await db.get_all_settings())
    warn_money_admins_not_admins()


async def set_setting(key: str, value: Any, actor_id: int | None) -> Any:
    """Сохраняет настройку. Возвращает прежнее значение (для журнала)."""
    if key not in SETTING_KEYS:
        raise KeyError(f"неизвестная настройка: {key}")
    old_value = get_raw(key)
    await db.set_setting(key, value, actor_id)
    cache_set(key, value)
    return old_value


def money_admins_not_in_admins() -> list[int]:
    return [a for a in get_settings().money_admins if a not in ADMINS]


def warn_money_admins_not_admins() -> None:
    """Без роли в ADMINS денежный админ не увидит админ-панель и заявки."""
    missing = money_admins_not_in_admins()
    if missing:
        logger.warning(f"⚠️ Денежные админы {missing} не входят в ADMINS: они не увидят админ-панель и заявки.")


def setup_problems() -> list[str]:
    """Чего не хватает для работы — для уведомления админам при старте."""
    settings = get_settings()
    problems: list[str] = []
    if settings.group_id is None:
        problems.append(GROUP_NOT_SET_TEXT + " Без него не работают реферальные ссылки и вывод средств.")
    if not settings.money_admins:
        problems.append(
            "⚠️ Не назначены денежные админы: одобрять выплаты некому. "
            "Укажите их в «👥 Пользователи» → «👑 Администраторы»."
        )
    return problems
