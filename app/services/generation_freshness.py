"""Shared freshness policy for pregenerated display artifacts."""

from __future__ import annotations

from datetime import datetime, timezone

GENERATION_SUCCESS_META_KEY = "last_generation_success"
GENERATION_STATUS_META_KEY = "last_generation_status"
GENERATION_STATUS_READY = "ready"
GENERATION_STATUS_DEGRADED = "degraded"

# Generation runs hourly. Six hours tolerates several transient failures while ensuring
# devices do not keep receiving an obsolete race indefinitely.
PREGENERATED_MAX_AGE_SECONDS = 6 * 60 * 60


def generation_age_seconds(
    generated_at: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Return the age of an ISO-8601 generation timestamp, or ``None`` if invalid."""
    if not generated_at:
        return None
    try:
        timestamp = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age = (current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    return max(0.0, age)


def generation_is_fresh(generated_at: str | None, *, now: datetime | None = None) -> bool:
    """Return whether a successful generation is within the serving tolerance."""
    age = generation_age_seconds(generated_at, now=now)
    return age is not None and age <= PREGENERATED_MAX_AGE_SECONDS
