# Файл: /www/wwwroot/uz_ibot/data/config.py
from pathlib import Path
import yaml
from environs import Env

# 📦 Инициализация переменных окружения
env = Env()
env.read_env()

# 🤖 Токен Telegram-бота
BOT_TOKEN: str = env.str("BOT_TOKEN")

# 👑 Список администраторов бота
ADMINS: list[int] = [int(a) for a in env.list("ADMINS")]

# 💰 ID администратора, отвечающего за выплаты
MONEY_ADMIN: int = env.int("MONEY_ADMIN")

# 👥 ID группы, куда ведут реферальные инвайт-ссылки
GROUP_ID: int = env.int("GROUP_ID")

# 📊 ID группы для логов (опционально)
LOG_GROUP_ID: int = env.int("LOG_GROUP_ID")

# 📁 Путь к директории с конфигурацией
config_dir = Path(__file__).parent.parent.resolve() / "data"

# 📄 Загрузка данных из файла url.yml (один раз при старте)
with open(config_dir / "url.yml", "r", encoding="utf-8") as f:
    url_yaml: dict = yaml.safe_load(f)

CHANNELS: list = url_yaml.get("channels", [])


def get_channels() -> list:
    """Возвращает актуальный список каналов из url.yml (перечитывает файл)."""
    with open(config_dir / "url.yml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("channels", [])
