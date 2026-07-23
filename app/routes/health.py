"""Liveness and dependency-aware readiness routes."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import config
from app.services.database import get_database
from app.services.generation_freshness import (
    GENERATION_STATUS_DEGRADED,
    GENERATION_STATUS_META_KEY,
    GENERATION_SUCCESS_META_KEY,
    generation_age_seconds,
    generation_is_fresh,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_CONTAINER_DATA_ROOT = Path("/app/data")


@router.get("/health")
async def health() -> dict[str, str]:
    """Report process liveness without depending on external resources."""
    return {"status": "healthy"}


def _storage_is_ready(database_path: str, images_path: str) -> tuple[bool, str]:
    """Check the container mount guard and perform a real write probe."""
    try:
        database_dir = Path(database_path).expanduser().resolve(strict=False).parent
        images_dir = Path(images_path).expanduser().resolve(strict=False)
        data_root = _CONTAINER_DATA_ROOT.resolve(strict=False)
        directories = (database_dir, images_dir)
        uses_container_root = any(directory.is_relative_to(data_root) for directory in directories)
        if uses_container_root and not os.path.ismount(data_root):
            return False, f"{data_root} is not a mounted volume"

        for directory in dict.fromkeys(directories):
            if not directory.is_dir():
                return False, f"{directory} does not exist"
            with tempfile.NamedTemporaryFile(prefix=".readiness-", dir=directory) as probe:
                probe.write(b"ready\n")
                probe.flush()
                os.fsync(probe.fileno())
    except OSError as exc:
        return False, f"storage write probe failed: {exc.__class__.__name__}"
    return True, "writable"


@router.get("/health/ready", response_class=JSONResponse)
async def readiness() -> JSONResponse:
    """Report whether persistent dependencies and generated artifacts are ready."""
    checks: dict[str, dict[str, object]] = {}
    database = None

    try:
        database = get_database()
        database_ok = await database.ping()
    except Exception as exc:
        logger.warning("Readiness database check failed: %s", exc)
        database_ok = False
    checks["database"] = {"ok": database_ok}

    storage_ok, storage_detail = await asyncio.to_thread(
        _storage_is_ready,
        config.DATABASE_PATH,
        config.IMAGES_PATH,
    )
    checks["storage"] = {"ok": storage_ok, "detail": storage_detail}

    generated_at: str | None = None
    last_generation_status: str | None = None
    if database_ok and database is not None:
        try:
            generated_at = await database.get_cache_meta(GENERATION_SUCCESS_META_KEY)
        except Exception as exc:
            logger.warning("Readiness generation timestamp check failed: %s", exc)
        if generated_at is not None:
            try:
                last_generation_status = await database.get_cache_meta(GENERATION_STATUS_META_KEY)
            except Exception as exc:
                logger.warning("Readiness generation status check failed: %s", exc)

    generation_age = generation_age_seconds(generated_at)
    generation_ok = generation_is_fresh(generated_at)
    if generated_at is None:
        generation_status = "starting"
    elif generation_age is None:
        generation_status = "invalid"
    elif not generation_ok:
        generation_status = "stale"
    elif last_generation_status == GENERATION_STATUS_DEGRADED:
        generation_status = "degraded"
    else:
        # A fresh marker written by a version predating status metadata remains valid.
        generation_status = "ready"
    checks["generation"] = {
        "ok": generation_ok,
        "status": generation_status,
        "age_seconds": generation_age,
    }

    ready = database_ok and storage_ok and generation_ok
    service_status = (
        "not_ready" if not ready else "degraded" if generation_status == "degraded" else "ready"
    )
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": service_status, "checks": checks},
    )
