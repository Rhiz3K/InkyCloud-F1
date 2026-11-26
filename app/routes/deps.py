"""Shared FastAPI dependencies for routers."""

from __future__ import annotations

from fastapi import Query

from app.services.f1_service import F1Service

from .params import validate_timezone


def get_f1_service(
    tz: str | None = Query(default=None, description="Timezone for F1Service"),
) -> F1Service:
    """Provide an `F1Service` instance for dependency injection."""
    # An empty ?tz= means "not set", as in the route-level validation; rejecting it here made
    # /calendar.bmp?tz= a 400 while /teams.bmp?tz= used the default.
    return F1Service(timezone_name=validate_timezone(tz))
