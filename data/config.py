from environs import Env

# 📦 Переменные окружения (#117): в .env остаются только секреты и доступ к базе.
# Группа, денежные админы, тарифы, тексты и каналы настраиваются в админ-панели
# и хранятся в базе (таблица bot_settings, см. data/settings.py).
env = Env()
env.read_env()

# 🤖 Токен Telegram-бота
BOT_TOKEN: str = env.str("BOT_TOKEN")

# 👑 Список администраторов бота
ADMINS: list[int] = [int(a) for a in env.list("ADMINS")]
