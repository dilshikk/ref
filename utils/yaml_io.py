# utils/yaml_io.py
"""Атомарная запись YAML: на диске всегда либо старая, либо новая полная версия файла."""
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def save_yaml_atomic(path: Path | str, data: Any) -> None:
    target = Path(path)
    # Временный файл в той же директории: os.replace атомарен только в пределах одной ФС
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp создаёт файл с правами 0600 — сохраняем права исходного файла
        try:
            os.chmod(tmp_name, target.stat().st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def save_url_yaml() -> None:
    """
    Сохраняет настройки (url_yaml) атомарно в URL_YML_PATH.

    Админ-панель (handlers/private/admin, #37) вызывает её напрямую. Раньше
    admin_panel._save_yaml перезаписывал файл на месте (open("w")), и падение во время
    записи оставляло пустой url.yml — бот не запускался, т.к. config.py читает его при импорте.
    """
    from data.config import URL_YML_PATH, url_yaml

    save_yaml_atomic(URL_YML_PATH, url_yaml)
