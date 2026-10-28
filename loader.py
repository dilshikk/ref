from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from environs import Env

from data.config import BOT_TOKEN
from utils.db.postgres import Database


env = Env()
env.read_env()


# Подключение к PostgreSQL
db = Database(
    host=env.str("DB_HOST"),
    user=env.str("DB_USER"),
    password=env.str("DB_PASS"),
    db_name=env.str("DB_NAME"),
    port=env.int("DB_PORT", 5432),
)


# Инициализация Telegram-бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
