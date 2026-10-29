from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from environs import Env

from data.config import BOT_TOKEN
from utils.db.antifraud_db import AntifraudDatabase


env = Env()
env.read_env()


# Подключение к PostgreSQL (SafeDatabase: исправленный commit + атомарные выплаты;
# ReferralDatabase: атомарные проверки при одобрении выплаты и удержание рефералов;
# BroadcastDatabase: рассылки с сохранением прогресса, #19;
# AntifraudDatabase: суточный лимит, капча, проверка добавленных, #18)
db = AntifraudDatabase(
    host=env.str("DB_HOST"),
    user=env.str("DB_USER"),
    password=env.str("DB_PASS"),
    db_name=env.str("DB_NAME"),
    port=env.int("DB_PORT", 5432),
)


# Инициализация Telegram-бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
