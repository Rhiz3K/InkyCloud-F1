"""Readiness policy tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routes import health
from app.services.generation_freshness import (
    PREGENERATED_MAX_AGE_SECONDS,
    generation_age_seconds,
    generation_is_fresh,
)


def _response_json(response) -> dict:
    return json.loads(response.body)


def test_generation_freshness_parses_boundaries_and_invalid_values():
    """Freshness parsing should be timezone-safe and reject unusable metadata."""
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    naive = (now - timedelta(hours=5)).replace(tzinfo=None).isoformat()

    assert generation_age_seconds(None, now=now) is None
    assert generation_age_seconds("not-a-date", now=now) is None
    assert generation_age_seconds(naive, now=now) == 5 * 3600
    assert generation_age_seconds((now + timedelta(minutes=1)).isoformat(), now=now) == 0
    assert generation_is_fresh(
        (now - timedelta(seconds=PREGENERATED_MAX_AGE_SECONDS)).isoformat(), now=now
    )
    assert not generation_is_fresh(
        (now - timedelta(seconds=PREGENERATED_MAX_AGE_SECONDS + 1)).isoformat(), now=now
    )


def test_storage_probe_accepts_writable_non_container_paths(tmp_path, monkeypatch):
    """Local-development paths do not require the Docker mount guard."""
    data_dir = tmp_path / "data"
    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")

    assert health._storage_is_ready(str(data_dir / "f1.db"), str(images_dir)) == (
        True,
        "writable",
    )
    assert not list(data_dir.rglob(".readiness-*"))


def test_storage_probe_rejects_missing_directory_and_write_failure(tmp_path, monkeypatch):
    """Missing or non-writable data directories make the service unready."""
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    missing = tmp_path / "missing"
    ok, detail = health._storage_is_ready(str(tmp_path / "f1.db"), str(missing))
    assert ok is False
    assert detail.endswith("does not exist")

    images_dir = tmp_path / "images"
    images_dir.mkdir()

    def deny_probe(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(health.tempfile, "NamedTemporaryFile", deny_probe)
    ok, detail = health._storage_is_ready(str(tmp_path / "f1.db"), str(images_dir))
    assert ok is False
    assert detail == "storage write probe failed: PermissionError"


def test_storage_probe_rejects_nested_path_without_data_mount(tmp_path, monkeypatch):
    """A nested path below the container data root cannot bypass the mount check."""
    data_root = tmp_path / "app" / "data"
    nested = data_root / "nested"
    images_dir = nested / "images"
    images_dir.mkdir(parents=True)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", data_root)
    monkeypatch.setattr(health.os.path, "ismount", lambda _path: False)

    ok, detail = health._storage_is_ready(str(nested / "f1.db"), str(images_dir))

    assert ok is False
    assert detail == f"{data_root} is not a mounted volume"


@pytest.mark.asyncio
async def test_readiness_accepts_successful_generation_between_three_and_six_hours(
    tmp_path, monkeypatch
):
    """Several failed hourly runs remain tolerated within the serving window."""
    data_dir = tmp_path / "data"
    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True)
    generated_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    database = SimpleNamespace(
        ping=AsyncMock(return_value=True),
        get_cache_meta=AsyncMock(return_value=generated_at),
    )
    monkeypatch.setattr(health, "get_database", lambda: database)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    monkeypatch.setattr(health.config, "DATABASE_PATH", str(data_dir / "f1.db"))
    monkeypatch.setattr(health.config, "IMAGES_PATH", str(images_dir))

    response = await health.readiness()

    assert response.status_code == 200
    assert _response_json(response)["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_rejects_generation_older_than_serving_tolerance(tmp_path, monkeypatch):
    """Readiness and pregenerated serving share the exact same six-hour limit."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    database = SimpleNamespace(
        ping=AsyncMock(return_value=True),
        get_cache_meta=AsyncMock(
            return_value=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        ),
    )
    monkeypatch.setattr(health, "get_database", lambda: database)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    monkeypatch.setattr(health.config, "DATABASE_PATH", str(tmp_path / "f1.db"))
    monkeypatch.setattr(health.config, "IMAGES_PATH", str(images_dir))

    response = await health.readiness()
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["checks"]["generation"]["ok"] is False
    assert payload["checks"]["generation"]["age_seconds"] > PREGENERATED_MAX_AGE_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("ping_result", [False, RuntimeError("database unavailable")])
async def test_readiness_rejects_database_failures_without_reading_metadata(
    tmp_path, monkeypatch, ping_result
):
    """A failed SQLite probe short-circuits the generation metadata query."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    ping = (
        AsyncMock(side_effect=ping_result)
        if isinstance(ping_result, Exception)
        else AsyncMock(return_value=ping_result)
    )
    database = SimpleNamespace(ping=ping, get_cache_meta=AsyncMock())
    monkeypatch.setattr(health, "get_database", lambda: database)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    monkeypatch.setattr(health.config, "DATABASE_PATH", str(tmp_path / "f1.db"))
    monkeypatch.setattr(health.config, "IMAGES_PATH", str(images_dir))

    response = await health.readiness()

    assert response.status_code == 503
    assert _response_json(response)["checks"]["database"]["ok"] is False
    database.get_cache_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_rejects_database_service_construction_failure(tmp_path, monkeypatch):
    """Failure to construct the shared database service is reported as unready."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    def fail_database():
        raise RuntimeError("database configuration unavailable")

    monkeypatch.setattr(health, "get_database", fail_database)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    monkeypatch.setattr(health.config, "DATABASE_PATH", str(tmp_path / "f1.db"))
    monkeypatch.setattr(health.config, "IMAGES_PATH", str(images_dir))

    response = await health.readiness()
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["checks"]["database"] == {"ok": False}
    assert payload["checks"]["generation"] == {"ok": False, "age_seconds": None}


@pytest.mark.asyncio
async def test_readiness_rejects_generation_metadata_query_failure(tmp_path, monkeypatch):
    """An unreadable generation marker cannot be treated as fresh."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    database = SimpleNamespace(
        ping=AsyncMock(return_value=True),
        get_cache_meta=AsyncMock(side_effect=RuntimeError("metadata unavailable")),
    )
    monkeypatch.setattr(health, "get_database", lambda: database)
    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    monkeypatch.setattr(health.config, "DATABASE_PATH", str(tmp_path / "f1.db"))
    monkeypatch.setattr(health.config, "IMAGES_PATH", str(images_dir))

    response = await health.readiness()

    assert response.status_code == 503
    assert _response_json(response)["checks"]["generation"] == {
        "ok": False,
        "age_seconds": None,
    }
