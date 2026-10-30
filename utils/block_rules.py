"""Кого можно блокировать из профиля пользователя.

Админов (ADMINS) блокировать нельзя: middlewares/blocked.py всё равно их пропускает,
поэтому блокировка ничего не меняла, а в профиле висел ложный статус «🚫 Заблокирован».
"""
from collections.abc import Collection


def can_block(target_id: int, admins: Collection[int]) -> bool:
    return target_id not in admins


def show_block_button(target_id: int, is_blocked: bool, admins: Collection[int]) -> bool:
    """Кнопка «Заблокировать» — только для обычных пользователей.
    «Разблокировать» показываем всем, чтобы снять уже поставленную блокировку с админа."""
    return is_blocked or can_block(target_id, admins)
