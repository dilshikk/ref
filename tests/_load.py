"""Загрузка модуля по пути: импорт пакетов utils/services тянет настройки бота (BOT_TOKEN и т.д.)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(relative_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    # dataclass и Enum ищут модуль в sys.modules
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
