"""Operator diagnostics shared by ``ops status`` and ``GET /api/ops/status``.

The report explains a degraded or unready service with stable ``DegradedReason`` codes. It is
read from one read-only SQLite snapshot and never contains visitor statistics, file paths,
hostnames, bucket names or secrets, so the same payload is safe for the CLI and the
token-protected HTTP endpoint. Run it with ``ops status`` (or ``python -m
app.services.ops_status``) inside the container.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import config
from app.services.database import WEATHER_QUOTA_LIMITS
from app.services.generation_freshness import (
    GENERATION_REASONS_META_KEY,
    GENERATION_STATE_REASONS,
    GENERATION_STATUS_META_KEY,
    GENERATION_SUCCESS_META_KEY,
    DegradedReason,
    GenerationState,
    classify_generation,
    decode_reasons,
    generation_age_seconds,
)
from app.utils.runtime_audit import WEATHER_WINDOWS
from app.utils.sqlite_readonly import read_consistently
from app.version import APP_VERSION

REPORT_SCHEMA_VERSION = 1
_HISTORICAL_REFRESH_META_NAME = "last_historical_refresh"
_HISTORICAL_REFRESH_STREAK_META_NAME = "historical_refresh_incomplete_streak"
# Matches the escalation threshold of the daily refresh job.
_HISTORICAL_REFRESH_ALERT_AFTER_RUNS = 3
# The refresh runs daily; two missed days mean the job is not completing.
_HISTORICAL_REFRESH_OVERDUE_AFTER = timedelta(days=2)
_NOT_READY_STATES = {GenerationState.STARTING, GenerationState.INVALID, GenerationState.STALE}
_META_KEYS = (
    GENERATION_SUCCESS_META_KEY,
    GENERATION_STATUS_META_KEY,
    GENERATION_REASONS_META_KEY,
    _HISTORICAL_REFRESH_META_NAME,
    _HISTORICAL_REFRESH_STREAK_META_NAME,
    "last_backup_success",
    "last_backup_failure",
)


def _read_rows(uri: str, now: datetime) -> dict[str, Any]:
    """Read the diagnostic rows through one read-only connection, in one transaction."""
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta: dict[str, str] = {}
        if "cache_meta" in tables:
            meta = dict(
                db.execute(
                    "SELECT key, value FROM cache_meta "
                    "WHERE key IN (SELECT value FROM json_each(?))",
                    (json.dumps(_META_KEYS),),
                ).fetchall()
            )
        weather: dict[str, int] = {}
        if "weather_api_requests" in tables:
            weather = {
                label: db.execute(
                    "SELECT COUNT(*) FROM weather_api_requests WHERE timestamp > ?",
                    (now.timestamp() - seconds,),
                ).fetchone()[0]
                for label, seconds in WEATHER_WINDOWS.items()
            }
        weekend: list[tuple] = []
        if "weekend_results" in tables:
            weekend = db.execute(
                "SELECT season, round, podium IS NOT NULL, checked_at FROM weekend_results"
            ).fetchall()
    return {"meta": meta, "weather": weather, "weekend": weekend}


def _read_database(path: Path, now: datetime) -> dict[str, Any] | None:
    """Read the diagnostic rows from one consistent snapshot; None if unreadable.

    The database is opened read-only and never initialized, so the command is safe while the
    service runs and while it is stopped.
    """
    try:
        return read_consistently(path, lambda uri: _read_rows(uri, now))
    except OSError, sqlite3.Error:
        return None


def _storage_ok() -> bool:
    """Run the readiness storage probe and keep only its verdict (the detail names paths)."""
    from app.routes.health import _storage_is_ready

    return _storage_is_ready(config.DATABASE_PATH, config.IMAGES_PATH)[0]


def _iso_or_none(raw: str | None) -> str | None:
    """Normalize a stored ISO timestamp to UTC, dropping unparsable values."""
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def _weather_section(counts: dict[str, int]) -> tuple[dict[str, Any], bool]:
    """Return weather quota usage per window and whether any window is exhausted."""
    exhausted = any(
        (used := counts.get(label)) is not None and used >= limit
        for label, limit in WEATHER_QUOTA_LIMITS.items()
    )
    windows = {
        label: {"used": counts.get(label), "limit": limit}
        for label, limit in WEATHER_QUOTA_LIMITS.items()
    }
    return {
        "enabled": config.WEATHER_ENABLED,
        "prefetch_all_circuits": config.WEATHER_PREFETCH_ALL_CIRCUITS,
        "quota": windows,
        "quota_exhausted": exhausted,
    }, exhausted


def _historical_section(meta: dict[str, str], now: datetime) -> tuple[dict[str, Any], list]:
    """Return the daily historical refresh state and its reason codes."""
    reasons: list[DegradedReason] = []
    try:
        streak = max(int(meta.get(_HISTORICAL_REFRESH_STREAK_META_NAME) or 0), 0)
    except ValueError:
        streak = 0
    last_success = _iso_or_none(meta.get(_HISTORICAL_REFRESH_META_NAME))
    if streak >= _HISTORICAL_REFRESH_ALERT_AFTER_RUNS:
        reasons.append(DegradedReason.HISTORICAL_REFRESH_INCOMPLETE)
    if (
        config.SCHEDULER_ENABLED
        and last_success is not None
        and now - datetime.fromisoformat(last_success) > _HISTORICAL_REFRESH_OVERDUE_AFTER
    ):
        reasons.append(DegradedReason.HISTORICAL_REFRESH_OVERDUE)
    return {"last_success_utc": last_success, "incomplete_streak": streak}, reasons


def _weekend_section(rows: list[tuple]) -> dict[str, Any]:
    """Summarize stored weekend podiums without their contents."""
    latest = max(((int(row[0]), int(row[1])) for row in rows), default=None)
    checked = [stamp for row in rows if (stamp := _iso_or_none(row[3]))]
    return {
        "stored_sessions": len(rows),
        "podiums": sum(1 for row in rows if row[2]),
        "pending": sum(1 for row in rows if not row[2]),
        "latest_round": {"season": latest[0], "round": latest[1]} if latest else None,
        "last_checked_utc": max(checked, default=None),
    }


def _disk_cache_section() -> dict[str, int]:
    """Count the pregenerated files on disk."""
    images_dir = Path(config.IMAGES_PATH)
    counts = {"bmp_files": 0, "png_files": 0, "bytes": 0}
    try:
        for path in images_dir.iterdir():
            if path.suffix in (".bmp", ".png") and path.is_file():
                counts[f"{path.suffix[1:]}_files"] += 1
                counts["bytes"] += path.stat().st_size
    except OSError:
        pass
    return counts


def _backup_section(meta: dict[str, str]) -> tuple[dict[str, Any], list]:
    """Return external and local backup state without endpoints, buckets or paths."""
    from app.services.backup import is_backup_configured, local_backups

    external_enabled = is_backup_configured()
    last_success = _iso_or_none(meta.get("last_backup_success"))
    last_failure = _iso_or_none(meta.get("last_backup_failure"))
    failed = (
        external_enabled
        and last_failure is not None
        and (
            last_success is None
            or datetime.fromisoformat(last_failure) > datetime.fromisoformat(last_success)
        )
    )
    try:
        local = [backup for backup in local_backups() if not backup["safety_snapshot"]]
    except OSError:
        local = []
    return {
        "external_enabled": external_enabled,
        "last_success_utc": last_success,
        "last_failure_utc": last_failure,
        "local_backups": len(local),
        "last_local_backup_utc": local[-1]["created_utc"] if local else None,
    }, [DegradedReason.BACKUP_FAILED] if failed else []


def pregeneration_profile() -> dict[str, Any]:
    """Describe the active pregeneration profile and how many files one hourly run writes."""
    languages = list(config.pregenerate_languages)
    displays = list(config.pregenerate_displays)
    weather_types = ["off", *config.pregenerate_weather_types]
    return {
        "languages": languages,
        "displays": displays,
        "weather_types": weather_types,
        "weather_prefetch_all_circuits": config.WEATHER_PREFETCH_ALL_CIRCUITS,
        "default": not (
            config.PREGENERATE_LANGUAGES
            or config.PREGENERATE_DISPLAYS
            or config.PREGENERATE_WEATHER_TYPES
        )
        and config.WEATHER_PREFETCH_ALL_CIRCUITS,
        "calendar_variants_per_run": len(languages) * len(displays) * len(weather_types),
        "teams_variants_per_run": len(languages) * len(displays),
    }


def collect_ops_status(
    *,
    memory_caches: dict[str, dict[str, int]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the operator report; ``memory_caches`` is only known inside the server."""
    measured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snapshot = _read_database(Path(config.DATABASE_PATH), measured_at)
    database_ok = snapshot is not None
    storage_ok = _storage_ok()
    meta: dict[str, str] = snapshot["meta"] if snapshot else {}

    reasons: list[DegradedReason] = []
    if not database_ok:
        reasons.append(DegradedReason.DATABASE_UNAVAILABLE)
    if not storage_ok:
        reasons.append(DegradedReason.STORAGE_UNAVAILABLE)

    generated_at = meta.get(GENERATION_SUCCESS_META_KEY)
    state = classify_generation(generated_at, meta.get(GENERATION_STATUS_META_KEY), now=now)
    generation_reasons = decode_reasons(meta.get(GENERATION_REASONS_META_KEY))
    if state == GenerationState.DEGRADED:
        reasons.extend(generation_reasons or [DegradedReason.GENERATION_DEGRADED])
    elif state in _NOT_READY_STATES and database_ok:
        reasons.append(GENERATION_STATE_REASONS[state])

    weather, quota_exhausted = _weather_section(snapshot["weather"] if snapshot else {})
    if quota_exhausted:
        reasons.append(DegradedReason.WEATHER_QUOTA_EXHAUSTED)
    historical, historical_reasons = _historical_section(meta, measured_at)
    reasons.extend(historical_reasons)
    backup, backup_reasons = _backup_section(meta)
    reasons.extend(backup_reasons)

    ready = database_ok and storage_ok and state not in _NOT_READY_STATES
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "report_schema": REPORT_SCHEMA_VERSION,
        "status": "not_ready" if not ready else "degraded" if unique_reasons else "ready",
        "reasons": [reason.value for reason in unique_reasons],
        "measured_at_utc": measured_at.isoformat(),
        "version": APP_VERSION,
        "minimal_data_mode": config.MINIMAL_DATA_MODE,
        "scheduler_enabled": config.SCHEDULER_ENABLED,
        "checks": {"database_ok": database_ok, "storage_ok": storage_ok},
        "generation": {
            "state": state.value,
            "last_success_utc": _iso_or_none(generated_at),
            "age_seconds": generation_age_seconds(generated_at, now=now),
            "reasons": [reason.value for reason in generation_reasons],
        },
        "weather": weather,
        "historical_refresh": historical,
        "weekend_results": _weekend_section(snapshot["weekend"] if snapshot else []),
        "cache": {"disk": _disk_cache_section(), "memory": memory_caches},
        "backup": backup,
        "pregeneration_profile": pregeneration_profile(),
    }


def _format_text(report: dict[str, Any]) -> str:
    """Render the report as short human-readable lines."""
    profile = report["pregeneration_profile"]
    quota = report["weather"]["quota"]
    weekend = report["weekend_results"]
    backup = report["backup"]
    disk = report["cache"]["disk"]
    lines = [
        f"Status:       {report['status']}",
        f"Reasons:      {', '.join(report['reasons']) or 'none'}",
        f"Version:      {report['version']}",
        f"Generation:   {report['generation']['state']} "
        f"(last success {report['generation']['last_success_utc'] or 'never'})",
        "Weather quota: "
        + ", ".join(
            f"{label} {window['used']}/{window['limit']}" for label, window in quota.items()
        ),
        f"Historical:   last success {report['historical_refresh']['last_success_utc'] or 'never'}"
        f", incomplete streak {report['historical_refresh']['incomplete_streak']}",
        f"Weekend:      {weekend['podiums']} podium(s), {weekend['pending']} pending",
        f"Disk cache:   {disk['bmp_files']} BMP, {disk['png_files']} PNG, {disk['bytes']} bytes",
        f"Backup:       external {'on' if backup['external_enabled'] else 'off'}, "
        f"last success {backup['last_success_utc'] or 'never'}, "
        f"{backup['local_backups']} local",
        f"Profile:      languages={','.join(profile['languages'])} "
        f"displays={','.join(profile['displays'])} "
        f"weather={','.join(profile['weather_types'])} "
        f"prefetch_all_circuits={str(profile['weather_prefetch_all_circuits']).lower()}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """``ops status [--json]``: exit 0 when ready, 1 when degraded, 2 when not ready."""
    parser = argparse.ArgumentParser(prog="ops", description="InkyCloud-F1 operator diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show readiness, reasons and resource usage")
    status.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    report = collect_ops_status()
    print(json.dumps(report, indent=2) if args.json else _format_text(report))
    return {"ready": 0, "degraded": 1}.get(report["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
