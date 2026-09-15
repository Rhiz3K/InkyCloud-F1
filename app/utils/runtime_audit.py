"""Read-only operational counts, without loading or printing individual records."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.config import config

USAGE_QUERIES = {
    "api_calls": "SELECT COUNT(*) FROM api_calls",
    "perf_metrics": "SELECT COUNT(*) FROM perf_metrics",
    "api_call_totals": "SELECT COUNT(*) FROM api_call_totals",
    "perf_buckets": "SELECT COUNT(*) FROM perf_buckets",
    "stats_batches": "SELECT COUNT(*) FROM stats_batches",
}
USAGE_TABLES = tuple(USAGE_QUERIES)
WEATHER_WINDOWS = {"minute": 60, "hour": 3600, "day": 86400, "rolling_31_days": 31 * 86400}


def audit_database(
    path: Path,
    *,
    minimal_mode: bool,
    aggregate_only: bool,
    now: datetime | None = None,
) -> dict:
    """Inspect one consistent SQLite/WAL snapshot; never initialize or migrate the database."""
    measured_at = now or datetime.now(timezone.utc)
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    findings = []
    counts: dict[str, int | None] = {}
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, query in USAGE_QUERIES.items():
            counts[table] = db.execute(query).fetchone()[0] if (table in tables) else None
            if counts[table] is None:
                findings.append(f"{table}: missing; initialize the application before auditing")
            elif counts[table] and (minimal_mode or table in USAGE_TABLES[:2]):
                findings.append(f"{table}: retained rows violate the requested collection profile")
        weather = {
            label: db.execute(
                "SELECT COUNT(*) FROM weather_api_requests WHERE timestamp > ?",
                (measured_at.timestamp() - seconds,),
            ).fetchone()[0]
            for label, seconds in WEATHER_WINDOWS.items()
        }
    if not minimal_mode and not aggregate_only:
        findings.append("AGGREGATE_STATS_ONLY: disabled; individual records may be written")
    return {
        "status": "needs_attention" if findings else "ok",
        "measured_at_utc": measured_at.astimezone(timezone.utc).isoformat(),
        "profile": {"minimal_data_mode": minimal_mode, "aggregate_only": aggregate_only},
        "row_counts": counts,
        "weather_attempts": weather,
        "findings": findings,
        "scope": "Current database rows only; excludes provider logs, backups and billing. "
        "The 31-day count is incomplete until 31 days after upgrading from daily retention.",
    }


def main(argv: list[str] | None = None) -> int:
    """Print counts and safe findings only; return nonzero for failed or unavailable checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(config.DATABASE_PATH))
    args = parser.parse_args(argv)
    try:
        report = audit_database(
            args.database,
            minimal_mode=config.MINIMAL_DATA_MODE,
            aggregate_only=config.AGGREGATE_STATS_ONLY,
        )
    except OSError, sqlite3.Error:
        print(
            json.dumps(
                {"status": "unavailable", "findings": ["Cannot read an initialized database"]}
            )
        )
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
