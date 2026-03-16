"""Shared FastAPI dependencies for routers."""

from __future__ import annotations

from fastapi import Query

from app.services.f1_service import F1Service


def get_f1_service(
    tz: str | None = Query(default=None, description="Timezone for F1Service"),
) -> F1Service:
    """Provide an `F1Service` instance for dependency injection."""
    return F1Service(timezone_name=tz)
