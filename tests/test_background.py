"""Тесты фоновых задач (#24). Запуск: python -m pytest tests"""
import asyncio

import pytest

pytest.importorskip("loguru")

from _load import load  # noqa: E402

bg = load("utils/background.py", "background_under_test")


def test_task_reference_kept_until_done():
    async def scenario():
        done = asyncio.Event()

        async def job():
            await asyncio.sleep(0.01)
            done.set()

        bg.spawn(job(), name="test-job")
        assert bg.pending_count() == 1
        await asyncio.wait_for(done.wait(), 1)
        await asyncio.sleep(0)  # дать отработать done-callback
        assert bg.pending_count() == 0

    asyncio.run(scenario())


def test_failed_task_is_released():
    async def scenario():
        async def boom():
            raise RuntimeError("x")

        task = bg.spawn(boom())
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)
        assert bg.pending_count() == 0

    asyncio.run(scenario())
