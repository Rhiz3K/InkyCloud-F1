"""Tests for supervised background task helpers."""

import asyncio
from unittest.mock import patch

import pytest

from app.utils.async_tasks import _background_tasks, create_supervised_task


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
