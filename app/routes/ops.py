"""Token-protected operator diagnostics.

``GET /api/ops/status`` exists only when ``ADMIN_API_TOKEN`` is configured; otherwise it answers
404 like any unknown path. Unlike the statistics APIs it never falls back to public access.
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import config
from app.services.ops_status import collect_ops_status
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils.rate_limit import enforce_rate_limit

router = APIRouter()
_NO_STORE = {"Cache-Control": "no-store"}


def _require_admin_bearer(request: Request) -> None:
    """Hide the endpoint without a configured token; demand that exact bearer token.

    The comparison runs in constant time over bytes, so non-ASCII header values are rejected
    with 401 instead of raising. ``X-Admin-Token`` is deliberately not accepted here.
    """
    configured = config.ADMIN_API_TOKEN
    expected = configured.get_secret_value() if configured is not None else ""
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    scheme, _, provided = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        provided.strip().encode("utf-8"), expected.encode("utf-8")
    ):
        enforce_rate_limit(request, bucket="ops_rejected", limit=config.STATS_RATE_LIMIT_PER_MINUTE)
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer", **_NO_STORE},
        )

    enforce_rate_limit(request, bucket="ops_read", limit=config.STATS_RATE_LIMIT_PER_MINUTE)


@router.get("/api/ops/status", include_in_schema=False)
async def ops_status(request: Request) -> JSONResponse:
    """Return the ``ops status`` report plus the in-process cache occupancy."""
    _require_admin_bearer(request)
    bmp_cache, variant_cache = get_bmp_cache(), get_bmp_variant_cache()
    memory = {
        "bmp": {"size": len(bmp_cache), "max_size": int(bmp_cache.maxsize)},
        "variant": {"size": len(variant_cache), "max_size": int(variant_cache.maxsize)},
    }
    report = await asyncio.to_thread(collect_ops_status, memory_caches=memory)
    return JSONResponse(report, headers=_NO_STORE)
