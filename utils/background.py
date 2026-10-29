# utils/background.py
"""
Фоновые задачи с сохранением ссылки (#24).

asyncio держит на задачу только слабую ссылку: если результат create_task никуда
не сохранить, сборщик мусора может удалить задачу до завершения (например, до
удаления приветствия в группе). Здесь ссылки лежат в множестве и убираются,
когда задача закончилась. Ошибки задачи пишутся в лог, а не теряются молча.
"""
import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

_tasks: set[asyncio.Task[Any]] = set()


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.opt(exception=error).error(f"Фоновая задача «{task.get_name()}» завершилась с ошибкой")


def spawn(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
    """Запускает корутину в фоне и хранит ссылку на задачу, пока она не завершится."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    return len(_tasks)
