"""Tests for supervised background task helpers."""

import asyncio
from unittest.mock import patch

import pytest

from app.utils.async_tasks import _background_tasks, create_supervised_task, drain_background_tasks


async def _flush_background_tasks() -> None:
    while _background_tasks:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_create_supervised_task_logs_and_captures_failures(caplog):
    async def failing_task() -> None:
        raise RuntimeError("boom")

    with (
        caplog.at_level("ERROR"),
        patch("app.utils.async_tasks.sentry_sdk.capture_exception") as capture_exception,
    ):
        create_supervised_task(failing_task(), name="failing_task")
        await _flush_background_tasks()
        capture_exception.assert_called_once()

    assert "Background task failed: failing_task" in caplog.text
    assert not _background_tasks


@pytest.mark.asyncio
async def test_create_supervised_task_tracks_successful_tasks() -> None:
    results: list[str] = []

    async def successful_task() -> None:
        results.append("done")

    create_supervised_task(successful_task(), name="successful_task")
    await _flush_background_tasks()

    assert results == ["done"]
    assert not _background_tasks


@pytest.mark.asyncio
async def test_drain_background_tasks_waits_for_supervised_tasks() -> None:
    results: list[str] = []

    async def successful_task() -> None:
        await asyncio.sleep(0)
        results.append("done")

    create_supervised_task(successful_task(), name="drained_task")
    await drain_background_tasks(timeout=1)

    assert results == ["done"]
    assert not _background_tasks


@pytest.mark.asyncio
async def test_drain_background_tasks_cancels_tasks_after_timeout() -> None:
    started = asyncio.Event()

    async def blocked_task() -> None:
        started.set()
        await asyncio.Event().wait()

    create_supervised_task(blocked_task(), name="blocked_task")
    await started.wait()
    await drain_background_tasks(timeout=0)

    assert not _background_tasks
