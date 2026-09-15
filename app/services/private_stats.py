"""Hourly usage totals and coarse performance histograms without visitor records."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import available_timezones

from app.config import LANGUAGE_CODES
from app.paths import ASSETS_DIR
from app.services.track_catalog import TrackAccent, TrackSource, TrackStyle

PAGES = frozenset(
    {
        "/",
        "/configure/calendar",
        "/configure/teams",
        "/credits",
        "/privacy",
        "/stats",
        "/changelog",
        "/api/docs/html",
        "/other",
    }
)
ENDPOINTS = PAGES | {"/calendar.bmp", "/teams.bmp"}
TIMEZONES = available_timezones()
METRICS = {"lcp_ms": "lcp", "cls": "cls", "fcp_ms": "fcp", "ttfb_ms": "ttfb", "inp_ms": "inp"}
TRACK_DIMENSIONS = {
    "track_style": frozenset(TrackStyle),
    "track_source": frozenset(TrackSource),
    "track_accent": frozenset(TrackAccent),
}
LEGACY_TRACK_OPTION = "legacy"
DIMENSIONS = (
    "timestamp",
    "endpoint",
    "lang",
    "tz",
    "year",
    "round",
    "display_type",
    *TRACK_DIMENSIONS,
    "race_name",
    "is_auto_selected",
    "status_code",
)
DDL = """
CREATE TABLE IF NOT EXISTS stats_batches (
    batch_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS perf_buckets (
    timestamp TEXT NOT NULL, page_path TEXT NOT NULL, metric TEXT NOT NULL,
    value REAL NOT NULL, sample_count INTEGER NOT NULL,
    PRIMARY KEY(timestamp, page_path, metric, value)
);
"""


def canonical_page(value: str, *, endpoint: bool = False) -> str:
    """Discard query strings, unknown paths and language prefixes before collection."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/other"
    path = urlsplit(value).path
    parts = path.split("/")
    if len(parts) > 1 and parts[1] in LANGUAGE_CODES:
        path = "/" + "/".join(parts[2:])
    path = path.rstrip("/") or "/"
    if path == "/configure":
        path = "/configure/calendar"
    return path if path in (ENDPOINTS if endpoint else PAGES) else "/other"


def hour_stamp(value: str | None = None) -> str:
    """Use UTC hour precision, including for imported historic measurements."""
    date = datetime.fromisoformat(value) if value else datetime.now(timezone.utc)
    return (
        date.replace(tzinfo=date.tzinfo or timezone.utc)
        .astimezone(timezone.utc)
        .replace(minute=0, second=0, microsecond=0)
        .isoformat()
    )


def bounded_number(value: Any, upper: float) -> float | None:
    """Accept finite numeric measurements, without coercing arbitrary text."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= upper else None


@lru_cache(maxsize=16)
def race_names(year: int) -> dict[int, str]:
    """Resolve public race labels from bundled sporting data, never request text."""
    path = ASSETS_DIR / "seasons" / f"{year}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(race["round"]): race["raceName"]
        for race in data["races"]
        if str(race.get("round", "")).isdigit()
    }


def normalize_call(call: dict) -> dict:
    """Keep only validated public dimensions and numeric totals in an hourly bucket."""
    year = call.get("year")
    year = (
        year
        if isinstance(year, int) and not isinstance(year, bool) and 1950 <= year <= 2100
        else None
    )
    race_round = call.get("round")
    race_round = (
        race_round
        if isinstance(race_round, int)
        and not isinstance(race_round, bool)
        and 1 <= race_round <= 30
        else None
    )
    duration = bounded_number(call.get("response_time_ms"), 3_600_000)
    status = call.get("status_code")
    status = status if isinstance(status, int) and 100 <= status <= 599 else 200
    return {
        "timestamp": hour_stamp(call.get("timestamp")),
        "endpoint": canonical_page(call.get("endpoint", ""), endpoint=True),
        "lang": call.get("lang") if call.get("lang") in LANGUAGE_CODES else None,
        "tz": call.get("tz") if call.get("tz") in TIMEZONES else None,
        "year": year,
        "round": race_round,
        "display_type": call.get("display_type")
        if call.get("display_type") in {"1bit", "bwr", "bwry", "spectra6"}
        else None,
        **normalize_track_options(call),
        "race_name": race_names(year).get(race_round)
        if year is not None and race_round is not None
        else None,
        "is_auto_selected": int(bool(call.get("is_auto_selected"))),
        "status_code": status,
        "response_time_ms": round(duration / 10) * 10 if duration is not None else None,
        "response_count": int(duration is not None),
        "response_min": round(duration / 10) * 10 if duration is not None else None,
        "response_max": round(duration / 10) * 10 if duration is not None else None,
        "response_size_bytes": int(
            bounded_number(call.get("response_size_bytes"), 100_000_000) or 0
        ),
        "sample_count": 1,
        "aggregate": True,
    }


def normalize_track_options(call: dict) -> dict[str, str | None]:
    """Keep catalogue choices only; absent historic calendar choices are legacy."""
    calendar = canonical_page(call.get("endpoint", ""), endpoint=True) == "/calendar.bmp"
    return {
        field: (
            value
            if isinstance(value := call.get(field), str) and value in choices
            else LEGACY_TRACK_OPTION
        )
        if calendar
        else None
        for field, choices in TRACK_DIMENSIONS.items()
    }


async def migrate_track_options(conn) -> None:
    """Add map dimensions and backfill existing calendar rows without inferring defaults."""
    updates = {
        "api_calls": """UPDATE api_calls SET
            track_style=COALESCE(NULLIF(track_style, ''), :legacy),
            track_source=COALESCE(NULLIF(track_source, ''), :legacy),
            track_accent=COALESCE(NULLIF(track_accent, ''), :legacy)
            WHERE endpoint='/calendar.bmp' AND (
                track_style IS NULL OR track_style='' OR
                track_source IS NULL OR track_source='' OR
                track_accent IS NULL OR track_accent='')""",
        "api_call_totals": """UPDATE api_call_totals SET
            track_style=COALESCE(NULLIF(track_style, ''), :legacy),
            track_source=COALESCE(NULLIF(track_source, ''), :legacy),
            track_accent=COALESCE(NULLIF(track_accent, ''), :legacy)
            WHERE endpoint='/calendar.bmp' AND (
                track_style IS NULL OR track_style='' OR
                track_source IS NULL OR track_source='' OR
                track_accent IS NULL OR track_accent='')""",
    }
    for table, query in updates.items():
        async with conn.execute(f"PRAGMA table_info({table})") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        for field in TRACK_DIMENSIONS:
            if field not in columns:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT")
        await conn.execute(query, {"legacy": LEGACY_TRACK_OPTION})


async def load_track_usage(conn, cutoff: str) -> dict[str, list[dict]]:
    """Count each map choice across retained calendar requests, including legacy totals."""
    queries = {
        "track_style": """SELECT COALESCE(NULLIF(track_style, ''), ?) AS choice,
            SUM(sample_count) AS count FROM api_calls_combined
            WHERE timestamp > ? AND endpoint='/calendar.bmp'
            GROUP BY choice ORDER BY count DESC, choice ASC""",
        "track_source": """SELECT COALESCE(NULLIF(track_source, ''), ?) AS choice,
            SUM(sample_count) AS count FROM api_calls_combined
            WHERE timestamp > ? AND endpoint='/calendar.bmp'
            GROUP BY choice ORDER BY count DESC, choice ASC""",
        "track_accent": """SELECT COALESCE(NULLIF(track_accent, ''), ?) AS choice,
            SUM(sample_count) AS count FROM api_calls_combined
            WHERE timestamp > ? AND endpoint='/calendar.bmp'
            GROUP BY choice ORDER BY count DESC, choice ASC""",
    }
    result = {}
    for field, query in queries.items():
        async with conn.execute(query, (LEGACY_TRACK_OPTION, cutoff)) as cursor:
            rows = await cursor.fetchall()
        result[f"{field}s"] = [{field: row["choice"], "count": row["count"]} for row in rows]
    return result


def accumulate(target: dict, call: dict) -> None:
    """Merge counts and timings without retaining any contributing request."""
    for name in ("response_time_ms", "response_size_bytes", "response_count", "sample_count"):
        target[name] = (target.get(name) or 0) + (call.get(name) or 0)
    for name, choose in (("response_min", min), ("response_max", max)):
        values = [value for value in (target.get(name), call.get(name)) if value is not None]
        target[name] = choose(values) if values else None


async def save_api_buckets(conn, calls: list[dict]) -> int:
    """Atomically merge hourly buckets and deduplicate server flush batches on retries."""
    groups: dict[str, list[dict]] = defaultdict(list)
    batch_id = str(uuid4())
    for call in calls:
        # Batch IDs identify server flushes; they are never issued to a visitor.
        call.setdefault("batch_id", batch_id)
        bucket = (
            {**call, **normalize_track_options(call)}
            if call.get("aggregate")
            else normalize_call(call)
        )
        groups[call["batch_id"]].append(bucket)
    inserted = 0
    await conn.execute("BEGIN IMMEDIATE")
    for key, buckets in groups.items():
        cursor = await conn.execute(
            "INSERT OR IGNORE INTO stats_batches VALUES (?, ?)", (key, hour_stamp())
        )
        if cursor.rowcount == 0:
            continue
        for bucket in buckets:
            await merge_api_bucket(conn, bucket)
            inserted += bucket["sample_count"]
    await conn.commit()
    return inserted


async def merge_api_bucket(conn, bucket: dict) -> None:
    """Update a public dimension group; SQLite IS compares nullable dimensions safely."""
    async with conn.execute(
        """SELECT rowid, * FROM api_call_totals
            WHERE timestamp IS :timestamp AND endpoint IS :endpoint
                AND lang IS :lang AND tz IS :tz AND year IS :year AND round IS :round
                AND display_type IS :display_type AND track_style IS :track_style
                AND track_source IS :track_source AND track_accent IS :track_accent
                AND race_name IS :race_name AND is_auto_selected IS :is_auto_selected
                AND status_code IS :status_code""",
        bucket,
    ) as cursor:
        existing = await cursor.fetchone()
    if existing is not None:
        merged = dict(existing)
        accumulate(merged, bucket)
        await conn.execute(
            """UPDATE api_call_totals SET response_time_ms=:response_time_ms,
                response_size_bytes=:response_size_bytes, response_count=:response_count,
                sample_count=:sample_count, response_min=:response_min, response_max=:response_max
                WHERE rowid=:rowid""",
            merged,
        )
    else:
        await conn.execute(
            """INSERT INTO api_call_totals (
                timestamp, endpoint, lang, tz, year, round, display_type,
                track_style, track_source, track_accent, race_name, is_auto_selected,
                status_code, response_time_ms, response_size_bytes, response_count,
                sample_count, response_min, response_max
            ) VALUES (
                :timestamp, :endpoint, :lang, :tz, :year, :round, :display_type,
                :track_style, :track_source, :track_accent, :race_name, :is_auto_selected,
                :status_code, :response_time_ms, :response_size_bytes, :response_count,
                :sample_count, :response_min, :response_max
            )""",
            bucket,
        )


async def save_performance(conn, page: str, values: dict, stamp: str | None = None) -> None:
    """Store histogram counts, with 50 ms timing and 0.01 CLS resolution."""
    rows = []
    for field, metric in METRICS.items():
        value = bounded_number(values.get(field), 10 if metric == "cls" else 60_000)
        if value is not None:
            rounded = round(value, 2) if metric == "cls" else round(value / 50) * 50
            rows.append((hour_stamp(stamp), canonical_page(page), metric, rounded, 1))
    if not rows:
        return
    rows.append((hour_stamp(stamp), canonical_page(page), "samples", 0, 1))
    await conn.executemany(
        "INSERT INTO perf_buckets VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(timestamp, page_path, metric, value) "
        "DO UPDATE SET sample_count=sample_count+1",
        rows,
    )


def summarize(histograms: dict[str, Counter]) -> dict:
    """Calculate weighted statistics directly from bounded histogram bins."""
    count = sum(histograms.get("samples", {}).values())
    result: dict[str, Any] = {
        "sample_count": count,
        "percentile_sample_count": count,
        "aggregation": "hourly-histogram",
        "timing_resolution_ms": 50,
        "cls_resolution": 0.01,
    }
    for metric in METRICS.values():
        bins = sorted(histograms.get(metric, {}).items())
        total = sum(n for _, n in bins)
        values: dict[str, Any] = {
            "avg": round(sum(value * n for value, n in bins) / total, 2 if metric == "cls" else 0)
            if total
            else None
        }
        for percentile in (50, 75, 95):
            remaining = max(1, math.ceil(total * percentile / 100))
            values[f"p{percentile}"] = None
            for value, n in bins:
                remaining -= n
                if remaining <= 0:
                    values[f"p{percentile}"] = value
                    break
        if metric in {"lcp", "ttfb"}:
            values.update(min=bins[0][0] if bins else None, max=bins[-1][0] if bins else None)
        result[metric] = values
    return result


async def load_performance(conn, hours: int, group: str = "all") -> dict[str, dict]:
    """Load aggregate bins by page/hour without expanding individual samples in memory."""
    query = {
        "all": """SELECT 'all' AS label, metric, value, SUM(sample_count) AS n
            FROM perf_buckets WHERE timestamp > ?
            GROUP BY label, metric, value ORDER BY label""",
        "page": """SELECT page_path AS label, metric, value, SUM(sample_count) AS n
            FROM perf_buckets WHERE timestamp > ?
            GROUP BY label, metric, value ORDER BY label""",
        "hour": """SELECT strftime('%Y-%m-%d %H:00', timestamp) AS label,
            metric, value, SUM(sample_count) AS n FROM perf_buckets WHERE timestamp > ?
            GROUP BY label, metric, value ORDER BY label""",
    }[group]
    cutoff = hour_stamp((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat())
    async with conn.execute(query, (cutoff,)) as cursor:
        rows = await cursor.fetchall()
    grouped: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for row in rows:
        grouped[row["label"]][row["metric"]][row["value"]] += row["n"]
    return {key: summarize(histograms) for key, histograms in grouped.items()}


async def migrate_records(conn) -> None:
    """Keep usable historic totals while removing old individual rows and identifiers."""
    await conn.execute("PRAGMA secure_delete=ON")
    await conn.execute("BEGIN IMMEDIATE")
    async with conn.execute(
        "SELECT timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz, year, "
        "round, display_type, track_style, track_source, track_accent, "
        "is_auto_selected, status_code FROM api_calls"
    ) as cursor:
        while rows := await cursor.fetchmany(500):
            for row in rows:
                await merge_api_bucket(conn, normalize_call(dict(row)))
    async with conn.execute(
        "SELECT timestamp, page_path, lcp_ms, cls, fcp_ms, ttfb_ms, inp_ms "
        "FROM perf_metrics WHERE measurement_version >= 2"
    ) as cursor:
        while rows := await cursor.fetchmany(500):
            for row in rows:
                await save_performance(conn, row["page_path"], dict(row), row["timestamp"])
    await conn.execute("DELETE FROM api_calls")
    await conn.execute("DELETE FROM perf_metrics")
    await conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('api_calls', 'perf_metrics')")
    await conn.commit()
    async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
        result = await cursor.fetchone()
    if result is not None and result[0] != 0:
        raise RuntimeError("Private statistics migration awaits exclusive database access")
