"""Shared FastAPI dependencies for routers."""

from __future__ import annotations

from fastapi import HTTPException, Query

from app.services.f1_service import F1Service
from app.utils.timezones import is_valid_timezone


def get_f1_service(
    tz: str | None = Query(default=None, description="Timezone for F1Service"),
) -> F1Service:
    """Provide an `F1Service` instance for dependency injection."""
    if tz is not None and not is_valid_timezone(tz):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}")
    return F1Service(timezone_name=tz)
