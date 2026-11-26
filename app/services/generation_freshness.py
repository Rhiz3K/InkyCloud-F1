"""Shared freshness policy for pregenerated display artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum

GENERATION_ATTEMPT_META_KEY = "last_generation_attempt"
GENERATION_SUCCESS_META_KEY = "last_generation_success"
GENERATION_STATUS_META_KEY = "last_generation_status"
# Comma-separated DegradedReason codes of the last generation run (empty after a clean run).
GENERATION_REASONS_META_KEY = "last_generation_degraded_reasons"
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


class GenerationState(StrEnum):
    """Readiness state of the pregenerated artifacts, as reported by ``/health/ready``."""

    STARTING = "starting"
    INVALID = "invalid"
    STALE = "stale"
    DEGRADED = "degraded"
    READY = "ready"


class DegradedReason(StrEnum):
    """Stable, non-identifying codes explaining why the service is degraded or not ready.

    Operators read them from ``ops status`` and ``GET /api/ops/status``. Codes are only ever
    added; renaming one would break monitoring that matches on it.
    """

    DATABASE_UNAVAILABLE = "database_unavailable"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    GENERATION_STARTING = "generation_starting"
    GENERATION_INVALID = "generation_invalid"
    GENERATION_STALE = "generation_stale"
    # A degraded run recorded by a version that did not store reasons yet.
    GENERATION_DEGRADED = "generation_degraded"
    CALENDAR_VARIANT_FAILED = "calendar_variant_failed"
    WEATHER_UNAVAILABLE = "weather_unavailable"
    WEATHER_VARIANT_FAILED = "weather_variant_failed"
    TIMEZONE_VARIANT_FAILED = "timezone_variant_failed"
    TEAMS_DATA_UNAVAILABLE = "teams_data_unavailable"
    TEAMS_VARIANT_FAILED = "teams_variant_failed"
    WEATHER_QUOTA_EXHAUSTED = "weather_quota_exhausted"
    HISTORICAL_REFRESH_INCOMPLETE = "historical_refresh_incomplete"
    HISTORICAL_REFRESH_OVERDUE = "historical_refresh_overdue"
    BACKUP_FAILED = "backup_failed"


# Each non-ready readiness state maps to exactly one reason code.
GENERATION_STATE_REASONS: dict[GenerationState, DegradedReason] = {
    GenerationState.STARTING: DegradedReason.GENERATION_STARTING,
    GenerationState.INVALID: DegradedReason.GENERATION_INVALID,
    GenerationState.STALE: DegradedReason.GENERATION_STALE,
    GenerationState.DEGRADED: DegradedReason.GENERATION_DEGRADED,
}


def classify_generation(
    generated_at: str | None,
    last_status: str | None,
    *,
    now: datetime | None = None,
) -> GenerationState:
    """Classify the generation marker the same way for readiness and diagnostics."""
    if generated_at is None:
        return GenerationState.STARTING
    age = generation_age_seconds(generated_at, now=now)
    if age is None:
        return GenerationState.INVALID
    if age > PREGENERATED_MAX_AGE_SECONDS:
        return GenerationState.STALE
    if last_status == GENERATION_STATUS_DEGRADED:
        return GenerationState.DEGRADED
    # A fresh marker written by a version predating status metadata remains valid.
    return GenerationState.READY


def encode_reasons(reasons: Iterable[DegradedReason]) -> str:
    """Serialize reason codes in their declaration order for ``cache_meta``."""
    selected = set(reasons)
    return ",".join(reason.value for reason in DegradedReason if reason in selected)


def decode_reasons(raw: str | None) -> list[DegradedReason]:
    """Parse stored reason codes, silently dropping codes this version does not know."""
    known = {reason.value: reason for reason in DegradedReason}
    return [known[code] for code in (raw or "").split(",") if code in known]
