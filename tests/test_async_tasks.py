"""Tests for supervised background task helpers."""

import asyncio
from unittest.mock import patch

from app.utils.async_tasks import _background_tasks, create_supervised_task


async def _flush_background_tasks() -> None:
    while _background_tasks:
        await asyncio.sleep(0)


def test_create_supervised_task_logs_and_captures_failures(caplog):
    async def failing_task() -> None:
        raise RuntimeError("boom")

    async def run_test() -> None:
        with patch("app.utils.async_tasks.sentry_sdk.capture_exception") as capture_exception:
            create_supervised_task(failing_task(), name="failing_task")
            await _flush_background_tasks()
            capture_exception.assert_called_once()

    with caplog.at_level("ERROR"):
        asyncio.run(run_test())

    assert "Background task failed: failing_task" in caplog.text
    assert not _background_tasks


def test_create_supervised_task_tracks_successful_tasks() -> None:
    results: list[str] = []

    async def successful_task() -> None:
        results.append("done")

    async def run_test() -> None:
        create_supervised_task(successful_task(), name="successful_task")
        await _flush_background_tasks()

    asyncio.run(run_test())

    assert results == ["done"]
    assert not _background_tasks
