"""Integration regressions for withdrawn assets, privacy and retention boundaries."""

import asyncio
import hashlib
import io
import json
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app.config import Config, config
from app.services import (
    artifact_metadata,
    legal,
    licensed_assets,
    scheduler_maintenance,
    weather_budget,
)
from app.services.database import Database
from scripts import check_legal_assets


@pytest.mark.parametrize(
    "field", ["RAW_STATS_RETENTION_DAYS", "STATS_RETENTION_DAYS", "BACKUP_RETENTION_DAYS"]
)
@pytest.mark.parametrize("days", [0, -1, 401])
def test_retention_cannot_be_disabled_with_zero(field, days):
    cfg = Config(_env_file=None, **{field: days})
    assert 1 <= getattr(cfg, field) <= 400


def test_error_scrubber_keeps_stack_location_without_private_context():
    event = {
        "event_id": "abc",
        "level": "error",
        "request": {"url": "private"},
        "user": {"ip_address": "private"},
        "breadcrumbs": ["private"],
        "tags": {"token": "private"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "module": "app",
                    "value": "private",
                    "mechanism": {"data": "private"},
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "app.test",
                                "function": "render",
                                "lineno": 2,
                                "in_app": True,
                                "filename": "private",
                                "vars": {"secret": "private"},
                                "context_line": "private",
                                "abs_path": "private",
                            }
                        ]
                    },
                }
            ]
        },
    }
    original = json.dumps(event)
    clean = legal.scrub_error_event(event)
    assert "private" not in json.dumps(clean)
    assert clean["exception"]["values"][0]["stacktrace"]["frames"][0]["lineno"] == 2
    assert json.dumps(event) == original
    assert legal.scrub_error_event({}) == {"exception": {"values": []}}
    assert legal.scrub_error_event({"exception": {"values": [{}]}})["exception"]["values"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status,attributed",
    [
        ("/calendar.bmp", 200, True),
        ("/teams.bmp", 304, True),
        ("/api/races/2026", 200, True),
        ("/preview/calendar.png", 200, True),
        ("/api/race/2026/1", 200, True),
        ("/calendar.bmp", 404, False),
        ("/health", 200, False),
    ],
)
async def test_attribution_travels_with_data_exports(path, status, attributed):
    messages = []

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"data"})

    await legal.AttributionMiddleware(application)(
        {"path": path}, AsyncMock(), AsyncMock(side_effect=messages.append)
    )
    headers = dict(messages[0]["headers"])
    assert (b"link" in headers) == attributed
    assert messages[1]["body"] == b"data"


def write_register(root, name, data):
    record = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "source": "own",
        "license": "MIT",
        "author": "test author",
    }
    (root / "asset-register.json").write_text(json.dumps({"assets": {name: record}}))
    return record


def test_static_allowlist_rejects_stale_unknown_changed_and_linked_files(tmp_path):
    asset = tmp_path / "good.txt"
    asset.write_bytes(b"reviewed")
    write_register(tmp_path, asset.name, b"reviewed")
    stale = tmp_path / "old-logo.png"
    stale.write_bytes(b"withdrawn")
    client = TestClient(
        Starlette(
            routes=[Mount("/static", licensed_assets.LicensedStaticFiles(directory=tmp_path))]
        )
    )
    response = client.get("/static/good.txt")
    assert response.status_code == 200
    assert (
        client.get(
            "/static/good.txt", headers={"If-None-Match": response.headers["etag"]}
        ).status_code
        == 304
    )
    assert client.get("/static/old-logo.png").status_code == 404
    assert client.get("/static/missing.png").status_code == 404
    asset.write_bytes(b"tampered")
    assert client.get("/static/good.txt").status_code == 404
    asset.unlink()
    asset.symlink_to(stale)
    write_register(tmp_path, asset.name, b"withdrawn")
    assert client.get("/static/good.txt").status_code == 404
    asset.unlink()
    asset.write_bytes(b"reviewed")
    (tmp_path / "asset-register.json").write_text("invalid")
    assert client.get("/static/good.txt").status_code == 404
    (tmp_path / "asset-register.json").unlink()
    assert client.get("/static/good.txt").status_code == 404


@pytest.mark.asyncio
async def test_previously_generated_team_and_calendar_art_is_invalidated(tmp_path):
    for name in ("calendar_en.bmp", "teams_en.bmp"):
        image = tmp_path / name
        image.write_bytes(b"BMP")
        await artifact_metadata.write_artifact_metadata(image, b"BMP", name)
        assert artifact_metadata.read_artifact_metadata(image)
        path = artifact_metadata.metadata_path(image)
        old = json.loads(path.read_text())
        old.pop("content_policy")
        path.write_text(json.dumps(old))
        assert artifact_metadata.read_artifact_metadata(image) is None


@pytest.mark.asyncio
async def test_rollup_preserves_weighted_totals_and_removes_individual_records(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "retention.db"))
    monkeypatch.setattr(config, "RAW_STATS_RETENTION_DAYS", 30)
    now = datetime.now(timezone.utc)
    try:
        rows = [
            {
                "timestamp": (now - timedelta(days=age)).isoformat(),
                "endpoint": "/calendar.bmp",
                "response_time_ms": elapsed,
                "response_size_bytes": 100,
                "lang": "cs",
                "tz": "Europe/Prague",
                "year": 2026,
                "round": 1,
                "race_name": "Test GP",
                "is_auto_selected": 1,
                "display_type": "bwry",
            }
            for age, elapsed in [(31, 100), (31, 300), (1, 200), (401, 999)]
        ]
        await db.save_api_calls_batch(rows)
        await db.save_perf_metric(
            "/",
            user_agent="private",
            connection_type="private",
            device_memory=8,
            visit_id="old-visitor",
        )
        before = await db.get_stats_for_range(400 * 24)
        assert before["total_requests"] == 3
        assert await db.cleanup_old_stats(400) == 3
        assert await db.get_stats_for_range(400 * 24) == before
        assert await db.cleanup_old_stats(400) == 0
        assert await db.get_stats_for_range(400 * 24) == before
        async with db._get_connection() as conn:
            async with conn.execute("SELECT COUNT(*) FROM api_calls") as cursor:
                assert (await cursor.fetchone())[0] == 1
            async with conn.execute("SELECT sample_count FROM api_call_totals") as cursor:
                assert [row[0] for row in await cursor.fetchall()] == [2]
            async with conn.execute(
                "SELECT user_agent, connection_type, device_memory, visit_id FROM perf_metrics"
            ) as cursor:
                assert tuple(await cursor.fetchone()) == (None, None, None, None)
        assert before["avg_response_ms"] == 200
        assert before["min_response_ms"] == 100
        assert before["max_response_ms"] == 300
        assert before["races"][0]["auto_selected_count"] == 3
        with pytest.raises(ValueError):
            await db.cleanup_old_stats(0)
        monkeypatch.setattr(scheduler_maintenance, "get_database", lambda: db)
        monkeypatch.setattr(config, "STATS_RETENTION_DAYS", 1)
        await scheduler_maintenance.cleanup_retained_stats()
        assert await db.get_api_calls_count(400 * 24) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,window", [(600, 60), (5000, 3600), (10000, 86400), (300000, 31 * 86400)]
)
async def test_weather_quota_is_shared_and_expires(tmp_path, count, window):
    db = Database(str(tmp_path / "quota.db"))
    now = 200000.0
    try:
        await db.ping()
        async with db._get_connection() as conn:
            await conn.executemany(
                "INSERT INTO weather_api_requests VALUES (?)", [(now - window + 1,)] * count
            )
            await conn.commit()
        assert not await db.reserve_weather_request(now)
        assert await db.reserve_weather_request(now + 1)
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,age", [(599, 0), (299999, 86400)])
async def test_weather_quota_reservation_is_atomic_across_instances(tmp_path, count, age):
    path = str(tmp_path / "shared.db")
    db, other = Database(path), Database(path)
    try:
        now = datetime.now(timezone.utc).timestamp()
        await db.ping()
        async with db._get_connection() as conn:
            await conn.executemany(
                "INSERT INTO weather_api_requests VALUES (?)", [(now - age,)] * count
            )
            await conn.commit()
        result = await asyncio.gather(db.reserve_weather_request(), other.reserve_weather_request())
        assert sorted(result) == [False, True]
        with (
            patch.object(weather_budget, "get_database", return_value=db),
            pytest.raises(RuntimeError, match="quota"),
        ):
            await weather_budget.WEATHER_BUDGET.wait()
        with patch.object(
            weather_budget,
            "get_database",
            return_value=Mock(reserve_weather_request=AsyncMock(return_value=True)),
        ):
            await weather_budget.WEATHER_BUDGET.wait()
        conn = await db._ensure_connection()
        with (
            patch.object(conn, "commit", AsyncMock(side_effect=RuntimeError("failed commit"))),
            pytest.raises(RuntimeError, match="failed commit"),
        ):
            await db.reserve_weather_request(now + 86400)
        assert not await db.reserve_weather_request(now)
    finally:
        await db.close()
        await other.close()


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_release_checker_reads_actual_archive_and_rejects_reintroduced_files(tmp_path, suffix):
    record = write_register(tmp_path, "good.txt", b"reviewed")
    register = {"good.txt": record}
    records = {
        "app/assets/good.txt": b"reviewed",
        "app/assets/asset-register.json": json.dumps({"assets": register}).encode(),
    }
    path = tmp_path / ("package" + suffix)
    if suffix == ".whl":
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in records.items():
                archive.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in records.items():
                entry = tarfile.TarInfo("package/" + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    assert check_legal_assets.archive_records(path) == records
    assert check_legal_assets.inspect_release(records, register, set()) == []
    records["artwork/tracks/old.psd"] = b"old"
    records["app/assets/unknown.txt"] = b"unknown"
    records["app/assets/good.txt"] = b"changed"
    records["app/assets/asset-register.json"] = b"{}"
    errors = check_legal_assets.inspect_release(records, register, set())
    assert len(errors) == 4
    assert check_legal_assets.inspect_assets({}, register) == ["Missing asset: good.txt"]
    assert check_legal_assets.inspect_assets(
        {"good.txt": b"reviewed"}, {"good.txt": {**record, "license": ""}}
    )


def test_invalid_runtime_circuit_record_preserves_valid_metadata(tmp_path, monkeypatch):
    from app.services import circuit_data

    bundled, runtime = tmp_path / "bundled.json", tmp_path / "runtime.json"
    bundled.write_text("{}")
    runtime.write_text('{"invalid": null, "valid": {"number_of_laps": 57}}')
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", bundled)
    result = circuit_data.load_circuits_data(runtime)
    assert result["invalid"] is None
    assert result["valid"]["number_of_laps"] == 57


def test_public_preflight_keeps_identity_private_and_reports_unresolved_settings():
    from scripts.check_public_deployment import findings

    settings = Config(
        _env_file=None,
        OPERATOR_NAME="",
        PRIVACY_CONTACT_EMAIL="",
        SENTRY_ENABLED=True,
        SENTRY_DSN="https://example.com/1",
        MONITORING_DETAILS="",
        BACKUP_ENABLED=True,
        BACKUP_DETAILS="",
        SCHEDULER_ENABLED=False,
        HETZNER_DPA_CONFIRMED=False,
    )
    messages = findings(settings)
    for key in [
        "OPERATOR_NAME",
        "PRIVACY_CONTACT_EMAIL",
        "HETZNER_DPA_CONFIRMED",
        "MONITORING_DETAILS",
        "BACKUP_DETAILS",
        "SCHEDULER_ENABLED",
    ]:
        assert any(message.startswith(key) for message in messages)
    settings.OPERATOR_NAME = "Example Association"
    settings.PRIVACY_CONTACT_EMAIL = "privacy@example.org"
    settings.HETZNER_DPA_CONFIRMED = True
    settings.SENTRY_ENABLED = False
    settings.BACKUP_ENABLED = False
    settings.SCHEDULER_ENABLED = True
    settings.SITE_URL = "https://racing.test"
    assert findings(settings) == []


@pytest.mark.parametrize(
    ("site_url", "flagged"),
    [
        ("https://example.com", True),
        ("https://racing.example.com", True),
        ("https://RACING.Example.COM.", True),
        ("https://example.net", True),
        ("https://racing.example.net", True),
        ("https://example.org", True),
        ("https://racing.example.org", True),
        ("https://localhost.", True),
        ("https://racing.localhost", True),
        ("http://racing.test", True),
        ("https:///credits", True),
        ("https://notexample.com", False),
        ("https://example.com.racing.test", False),
        ("https://racing.test/example.com", False),
    ],
)
def test_public_preflight_matches_placeholder_hostname_boundaries(site_url, flagged):
    """Reject placeholder origins without mistaking unrelated domains or paths for them."""
    from scripts.check_public_deployment import findings

    settings = config.model_copy(update={"SITE_URL": site_url})
    assert any(message.startswith("SITE_URL:") for message in findings(settings)) is flagged
