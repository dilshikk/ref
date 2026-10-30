# handlers/private/admin/__init__.py
"""
Админ-панель (#37). Раньше всё жило в одном handlers/private/admin_panel.py (110 КБ).

Порядок важен:
- разделы подключаются по кнопкам меню; тексты кнопок у разделов не пересекаются;
- forms_router (ввод значений в формах AdminFlow.*) — ПОСЛЕДНИМ: кнопка меню
  посреди формы открывает свой раздел, а не уходит в форму как значение.

Разделы «🤖 Бот» и «🔒 Безопасность» убраны: их настройки повторяли другие разделы.
Тексты, опрос и команды меню (bot.py) теперь открываются из «⚙️ Настройки».

Снаружи пакет подключается в handlers/__init__.py на прежнее место admin_panel:
после payout_guard, referral_holds и broadcasts (они перехватывают свои кнопки раньше).
"""
from aiogram import Router

from . import bot, broadcasts, channels, finance, finance_reports, logs, menu, settings, stats, users
from .common import forms_router

router = Router(name="private.admin")
router.include_routers(
    menu.router,
    stats.router,
    finance.router,
    # #68, #71, #72: «💳 Балансы», «🎁 Бонусы», «📊 Отчёты»
    finance_reports.router,
    users.router,
    channels.router,
    bot.router,
    broadcasts.router,
    settings.router,
    logs.router,
    forms_router,
)

__all__ = ["router"]
