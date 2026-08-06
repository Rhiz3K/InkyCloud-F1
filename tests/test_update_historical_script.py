"""Tests for the historical-refresh command-line wrapper."""

import pytest

from scripts import update_historical


@pytest.mark.asyncio
async def test_run_preserves_refresh_error_when_cleanup_also_fails(monkeypatch):
    async def fail_refresh(_circuit):
        raise RuntimeError("refresh failed")

    async def fail_cleanup():
        raise OSError("cleanup failed")

    monkeypatch.setattr(update_historical, "main", fail_refresh)
    monkeypatch.setattr(update_historical, "close_shared_http_clients", fail_cleanup)

    with pytest.raises(RuntimeError, match="refresh failed") as exc_info:
        await update_historical.run(None)

    assert exc_info.value.__notes__ == [
        "Shared HTTP client cleanup also failed: OSError('cleanup failed')"
    ]


@pytest.mark.asyncio
async def test_run_propagates_cleanup_error_after_success(monkeypatch):
    async def successful_refresh(_circuit):
        return None

    async def fail_cleanup():
        raise OSError("cleanup failed")

    monkeypatch.setattr(update_historical, "main", successful_refresh)
    monkeypatch.setattr(update_historical, "close_shared_http_clients", fail_cleanup)

    with pytest.raises(OSError, match="cleanup failed"):
        await update_historical.run("monza")
