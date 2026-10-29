from pathlib import Path
import yaml
from environs import Env
from loguru import logger

# 📦 Инициализация переменных окружения
env = Env()
env.read_env()

# 🤖 Токен Telegram-бота
BOT_TOKEN: str = env.str("BOT_TOKEN")

# 👑 Список администраторов бота
ADMINS: list[int] = [int(a) for a in env.list("ADMINS")]

# 💰 ID администратора, отвечающего за выплаты
MONEY_ADMIN: int = env.int("MONEY_ADMIN")

# 💰 Кто может одобрять/отклонять выплаты и менять балансы (#23).
# Всегда MONEY_ADMIN; дополнительно — необязательный список MONEY_ADMINS через запятую.
MONEY_ADMINS: list[int] = sorted({MONEY_ADMIN, *(int(a) for a in env.list("MONEY_ADMINS", []))})

# 👥 ID группы, куда ведут реферальные инвайт-ссылки.
# Это ЕДИНСТВЕННЫЙ источник ID группы: в ней же считаются «добавленные участники»
# для вывода средств.
GROUP_ID: int = env.int("GROUP_ID")

# 📊 ID группы для логов (опционально): без переменной логи в группу не отправляются (#51)
LOG_GROUP_ID: int | None = env.int("LOG_GROUP_ID", None)

# 📁 Путь к директории с конфигурацией
config_dir = Path(__file__).parent.parent.resolve() / "data"

# 📄 Путь к url.yml. В Docker файл лежит в отдельной смонтированной директории
# (URL_YML_PATH=/app/config/url.yml): тогда админка сохраняет его атомарно через
# временный файл + os.replace, а это невозможно, если смонтирован один файл.
# Без переменной используется старое место — data/url.yml.
URL_YML_PATH: Path = Path(env.str("URL_YML_PATH", "") or (config_dir / "url.yml"))


def _load_url_yaml() -> dict:
    with open(URL_YML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"{URL_YML_PATH} пустой или повреждён: ожидается YAML-словарь")
    return data


# 📄 Загрузка данных из файла url.yml (один раз при старте)
url_yaml: dict = _load_url_yaml()

CHANNELS: list = url_yaml.get("channels", [])


def _warn_group_mismatch() -> None:
    """Раньше проверка участников шла по subcr_chat_id, а счёт — по GROUP_ID.
    Теперь везде GROUP_ID; предупреждаем, если в url.yml осталось другое значение."""
    raw = url_yaml.get("subcr_chat_id")
    if raw in (None, ""):
        return
    try:
        same = int(raw) == GROUP_ID
    except (TypeError, ValueError):
        same = False
    if not same:
        logger.warning(
            f"⚠️ subcr_chat_id={raw} в url.yml не совпадает с GROUP_ID={GROUP_ID} в .env. "
            "Участники для вывода считаются по GROUP_ID."
        )


_warn_group_mismatch()


def _warn_money_admin_not_admin() -> None:
    """Без роли в ADMINS денежный админ не увидит админ-панель и заявки."""
    missing = [a for a in MONEY_ADMINS if a not in ADMINS]
    if missing:
        logger.warning(f"⚠️ MONEY_ADMINS {missing} нет в ADMINS: они не увидят админ-панель и заявки на вывод.")


_warn_money_admin_not_admin()


def get_channels() -> list:
    """Возвращает актуальный список каналов из url.yml (перечитывает файл)."""
    return _load_url_yaml().get("channels", [])
