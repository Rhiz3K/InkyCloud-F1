"""Operator diagnostics: ``ops status`` CLI and the token-protected HTTP endpoint (#185 F4)."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import app
from app.routes import ops
from app.services import ops_status
from app.services.database import WEATHER_QUOTA_LIMITS
from app.services.generation_freshness import (
    GENERATION_STATE_REASONS,
    PREGENERATED_MAX_AGE_SECONDS,
    DegradedReason,
    GenerationState,
    classify_generation,
    decode_reasons,
    encode_reasons,
)

# Real time: the HTTP endpoint and CLI measure against the clock.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the configuration at an isolated data directory with an initialized schema."""
    images = tmp_path / "images"
    images.mkdir()
    db_path = tmp_path / "f1.db"
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript(
            """
            CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE weather_api_requests (timestamp REAL NOT NULL);
            CREATE TABLE weekend_results (
                season INTEGER, round INTEGER, session TEXT, podium TEXT, checked_at TEXT,
                PRIMARY KEY (season, round, session)
            );
            CREATE TABLE api_calls (secret TEXT);
            INSERT INTO api_calls VALUES ('PRIVATE_SENTINEL');
            """
        )
        db.commit()
    from app.routes import health
    from app.services import backup

    monkeypatch.setattr(health, "_CONTAINER_DATA_ROOT", tmp_path / "unrelated-root")
    # Modules imported after a config reload hold another Config object; share one.
    monkeypatch.setattr(backup, "config", ops_status.config)
    monkeypatch.setattr(health, "config", ops_status.config)
    monkeypatch.setattr(ops_status.config, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(ops_status.config, "IMAGES_PATH", str(images))
    monkeypatch.setattr(ops_status.config, "BACKUP_LOCAL_DIR", "")
    monkeypatch.setattr(ops_status.config, "BACKUP_ENABLED", False)
    monkeypatch.setattr(ops_status.config, "SCHEDULER_ENABLED", True)
    return tmp_path


def _meta(db_path: Path, **values: str) -> None:
    with closing(sqlite3.connect(db_path)) as db:
        db.executemany("INSERT OR REPLACE INTO cache_meta VALUES (?, ?, 'x')", list(values.items()))
        db.commit()


def _ready_meta(db_path: Path) -> None:
    _meta(
        db_path,
        last_generation_success=_iso(timedelta(minutes=5)),
        last_generation_status="ready",
        last_generation_degraded_reasons="",
        last_historical_refresh=_iso(timedelta(hours=5)),
        historical_refresh_incomplete_streak="0",
    )


# --- shared enums ------------------------------------------------------------------------


def test_generation_states_and_reason_codes_are_stable():
    assert [state.value for state in GenerationState] == [
        "starting",
        "invalid",
        "stale",
        "degraded",
        "ready",
    ]
    assert {reason.value for reason in GENERATION_STATE_REASONS.values()} == {
        "generation_starting",
        "generation_invalid",
        "generation_stale",
        "generation_degraded",
    }
    assert all(reason.value == reason.name.lower() for reason in DegradedReason)


@pytest.mark.parametrize(
    ("generated_at", "status", "expected"),
    [
        (None, None, GenerationState.STARTING),
        ("garbage", None, GenerationState.INVALID),
        (_iso(timedelta(seconds=PREGENERATED_MAX_AGE_SECONDS + 1)), None, GenerationState.STALE),
        (_iso(timedelta(minutes=1)), "degraded", GenerationState.DEGRADED),
        (_iso(timedelta(minutes=1)), "ready", GenerationState.READY),
        (_iso(timedelta(minutes=1)), None, GenerationState.READY),
    ],
)
def test_classify_generation_matches_readiness(generated_at, status, expected):
    assert classify_generation(generated_at, status, now=NOW) == expected


def test_reason_codes_round_trip_in_declaration_order_and_drop_unknown_codes():
    encoded = encode_reasons(
        [DegradedReason.TEAMS_VARIANT_FAILED, DegradedReason.WEATHER_UNAVAILABLE]
    )

    assert encoded == "weather_unavailable,teams_variant_failed"
    assert decode_reasons(encoded + ",from_the_future") == [
        DegradedReason.WEATHER_UNAVAILABLE,
        DegradedReason.TEAMS_VARIANT_FAILED,
    ]
    assert decode_reasons(None) == []


# --- report ------------------------------------------------------------------------------


def test_ready_report_lists_every_section_without_private_data(data_dir):
    db_path = data_dir / "f1.db"
    _ready_meta(db_path)
    with closing(sqlite3.connect(db_path)) as db:
        db.executemany(
            "INSERT INTO weather_api_requests VALUES (?)",
            [(NOW.timestamp() - age,) for age in (10, 1000, 50000, 30 * 86400)],
        )
        db.executemany(
            "INSERT INTO weekend_results VALUES (?, ?, ?, ?, ?)",
            [
                (
                    2026,
                    21,
                    "qualifying",
                    '[{"driver": "PRIVATE_PODIUM"}]',
                    _iso(timedelta(hours=3)),
                ),
                (2026, 22, "race", None, _iso(timedelta(hours=1)).removesuffix("+00:00")),
                (2026, 22, "qualifying", None, "not-a-date"),
            ],
        )
        db.commit()
    (data_dir / "images" / "calendar_en.bmp").write_bytes(b"BM" + b"0" * 10)
    (data_dir / "images" / "configure_calendar_en.png").write_bytes(b"P" * 4)
    (data_dir / "images" / "calendar_en.bmp.etag").write_text("ignored")

    report = ops_status.collect_ops_status(memory_caches={"bmp": {"size": 1}}, now=NOW)

    assert report["status"] == "ready"
    assert report["reasons"] == []
    assert report["checks"] == {"database_ok": True, "storage_ok": True}
    assert report["generation"]["state"] == "ready"
    assert report["weather"]["quota"]["minute"] == {"used": 1, "limit": 600}
    assert report["weather"]["quota"]["rolling_31_days"]["used"] == 4
    assert report["historical_refresh"] == {
        "last_success_utc": _iso(timedelta(hours=5)),
        "incomplete_streak": 0,
    }
    assert report["weekend_results"] == {
        "stored_sessions": 3,
        "podiums": 1,
        "pending": 2,
        "latest_round": {"season": 2026, "round": 22},
        "last_checked_utc": _iso(timedelta(hours=1)),
    }
    assert report["cache"] == {
        "disk": {"bmp_files": 1, "png_files": 1, "bytes": 16},
        "memory": {"bmp": {"size": 1}},
    }
    assert report["backup"]["external_enabled"] is False
    assert report["pregeneration_profile"]["default"] is True
    assert report["pregeneration_profile"]["calendar_variants_per_run"] == 13 * 4 * 3

    payload = json.dumps(report)
    assert str(data_dir) not in payload
    assert "PRIVATE" not in payload
    assert not _IPV4.search(payload)


def test_degraded_generation_reports_stored_reason_codes(data_dir):
    _ready_meta(data_dir / "f1.db")
    _meta(
        data_dir / "f1.db",
        last_generation_status="degraded",
        last_generation_degraded_reasons="weather_unavailable,teams_data_unavailable",
    )

    report = ops_status.collect_ops_status(now=NOW)

    assert report["status"] == "degraded"
    assert report["reasons"] == ["weather_unavailable", "teams_data_unavailable"]
    assert report["generation"]["reasons"] == report["reasons"]


def test_degraded_marker_from_an_older_version_has_a_generic_code(data_dir):
    _ready_meta(data_dir / "f1.db")
    _meta(
        data_dir / "f1.db", last_generation_status="degraded", last_generation_degraded_reasons=""
    )

    assert ops_status.collect_ops_status(now=NOW)["reasons"] == ["generation_degraded"]


@pytest.mark.parametrize(
    ("generated_at", "reason"),
    [(None, "generation_starting"), ("bad", "generation_invalid"), ("stale", "generation_stale")],
)
def test_not_ready_generation_states_map_to_reason_codes(data_dir, generated_at, reason):
    if generated_at == "stale":
        generated_at = _iso(timedelta(hours=7))
    if generated_at is not None:
        _meta(data_dir / "f1.db", last_generation_success=generated_at)

    report = ops_status.collect_ops_status(now=NOW)

    assert report["status"] == "not_ready"
    assert report["reasons"] == [reason]


def test_unreadable_database_and_storage_are_not_ready(data_dir, monkeypatch):
    monkeypatch.setattr(ops_status.config, "DATABASE_PATH", str(data_dir / "missing" / "f1.db"))
    monkeypatch.setattr(ops_status.config, "IMAGES_PATH", str(data_dir / "no-images"))

    report = ops_status.collect_ops_status(now=NOW)

    assert report["status"] == "not_ready"
    assert report["reasons"] == ["database_unavailable", "storage_unavailable"]
    assert report["cache"]["disk"] == {"bmp_files": 0, "png_files": 0, "bytes": 0}
    assert report["weather"]["quota"]["hour"] == {"used": None, "limit": 5000}
    assert report["weekend_results"]["latest_round"] is None


def test_database_without_application_tables_is_readable(data_dir, monkeypatch):
    empty = data_dir / "empty.db"
    sqlite3.connect(empty).close()
    monkeypatch.setattr(ops_status.config, "DATABASE_PATH", str(empty))

    report = ops_status.collect_ops_status(now=NOW)

    assert report["checks"]["database_ok"] is True
    assert report["reasons"] == ["generation_starting"]


def test_exhausted_weather_quota_is_a_reason(data_dir):
    _ready_meta(data_dir / "f1.db")
    with closing(sqlite3.connect(data_dir / "f1.db")) as db:
        db.executemany(
            "INSERT INTO weather_api_requests VALUES (?)",
            [(NOW.timestamp() - 1,)] * WEATHER_QUOTA_LIMITS["minute"],
        )
        db.commit()

    report = ops_status.collect_ops_status(now=NOW)

    assert report["weather"]["quota_exhausted"] is True
    assert report["reasons"] == ["weather_quota_exhausted"]


def test_historical_refresh_streak_and_overdue_are_reasons(data_dir, monkeypatch):
    _ready_meta(data_dir / "f1.db")
    _meta(
        data_dir / "f1.db",
        historical_refresh_incomplete_streak="3",
        last_historical_refresh=_iso(timedelta(days=3)),
    )

    report = ops_status.collect_ops_status(now=NOW)
    assert report["reasons"] == ["historical_refresh_incomplete", "historical_refresh_overdue"]

    monkeypatch.setattr(ops_status.config, "SCHEDULER_ENABLED", False)
    _meta(data_dir / "f1.db", historical_refresh_incomplete_streak="nonsense")
    report = ops_status.collect_ops_status(now=NOW)
    assert report["reasons"] == []
    assert report["historical_refresh"]["incomplete_streak"] == 0


def test_failed_external_backup_is_a_reason_and_local_backups_are_counted(data_dir, monkeypatch):
    _ready_meta(data_dir / "f1.db")
    _meta(
        data_dir / "f1.db",
        last_backup_success=_iso(timedelta(days=2)),
        last_backup_failure=_iso(timedelta(days=1)),
    )
    backups = data_dir / "backups"
    backups.mkdir()
    (backups / "f1_backup_2026-11-20_03-00-00.db").write_bytes(b"x")
    (backups / "f1_backup_2026-11-21_03-00-00.db").write_bytes(b"x")
    (backups / "f1_pre_restore_2026-11-21_04-00-00.db").write_bytes(b"x")
    (backups / "notes.txt").write_text("ignored")
    monkeypatch.setattr("app.services.backup.is_backup_configured", lambda: True)

    report = ops_status.collect_ops_status(now=NOW)

    assert report["reasons"] == ["backup_failed"]
    assert report["backup"] == {
        "external_enabled": True,
        "last_success_utc": _iso(timedelta(days=2)),
        "last_failure_utc": _iso(timedelta(days=1)),
        "local_backups": 2,
        "last_local_backup_utc": "2026-11-21T03:00:00+00:00",
    }

    monkeypatch.setattr("app.services.backup.is_backup_configured", lambda: False)
    assert ops_status.collect_ops_status(now=NOW)["reasons"] == []


def test_unreadable_local_backup_directory_counts_as_none(data_dir, monkeypatch):
    def broken():
        raise PermissionError("denied")

    monkeypatch.setattr("app.services.backup.local_backups", broken)

    assert ops_status.collect_ops_status(now=NOW)["backup"]["local_backups"] == 0


def test_custom_profile_is_reported(monkeypatch, data_dir):
    monkeypatch.setattr(ops_status.config, "PREGENERATE_LANGUAGES", "cs")
    monkeypatch.setattr(ops_status.config, "PREGENERATE_DISPLAYS", "bwry")
    monkeypatch.setattr(ops_status.config, "PREGENERATE_WEATHER_TYPES", "off")
    monkeypatch.setattr(ops_status.config, "WEATHER_PREFETCH_ALL_CIRCUITS", False)

    assert ops_status.pregeneration_profile() == {
        "languages": ["cs"],
        "displays": ["bwry"],
        "weather_types": ["off"],
        "weather_prefetch_all_circuits": False,
        "default": False,
        "calendar_variants_per_run": 1,
        "teams_variants_per_run": 1,
    }


def test_read_leaves_no_sidecar_files_on_a_stopped_wal_database(data_dir):
    db_path = data_dir / "f1.db"
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=WAL")

    ops_status.collect_ops_status(now=NOW)

    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()


# --- CLI ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "exit_code"), [("ready", 0), ("degraded", 1), ("not_ready", 2)])
def test_cli_exit_codes_and_json_output(data_dir, monkeypatch, capsys, status, exit_code):
    report = ops_status.collect_ops_status(now=NOW)
    report["status"] = status
    monkeypatch.setattr(ops_status, "collect_ops_status", lambda: report)

    assert ops_status.main(["status", "--json"]) == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_text_output_is_short_and_pathless(data_dir, capsys):
    _ready_meta(data_dir / "f1.db")

    assert ops_status.main(["status"]) == 0

    output = capsys.readouterr().out
    assert output.splitlines()[0].split() == ["Status:", "ready"]
    assert "Reasons:      none" in output
    assert "Profile:      languages=cs,de" in output
    assert str(data_dir) not in output


def test_cli_requires_a_command(capsys):
    with pytest.raises(SystemExit):
        ops_status.main([])


# --- HTTP --------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, data_dir):
    from app.utils import rate_limit

    monkeypatch.setattr(ops, "config", ops_status.config)
    monkeypatch.setattr(rate_limit, "config", ops_status.config)
    monkeypatch.setattr(ops.config, "RATE_LIMIT_ENABLED", False)
    return TestClient(app)


@pytest.mark.parametrize("token", [None, SecretStr("")])
def test_endpoint_is_hidden_without_a_configured_token(client, monkeypatch, token):
    monkeypatch.setattr(ops.config, "ADMIN_API_TOKEN", token)

    response = client.get("/api/ops/status", headers={"Authorization": "Bearer anything"})
    unknown = client.get("/api/ops/does-not-exist")

    assert response.status_code == 404
    assert response.json() == unknown.json()
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic b3BzLXRva2Vu"},
        {"Authorization": "Bearer"},
        {"X-Admin-Token": "ops-token"},
        {"Authorization": "Bearer ops-tokén".encode()},
    ],
)
def test_wrong_or_missing_token_is_rejected_without_details(client, monkeypatch, headers):
    monkeypatch.setattr(ops.config, "ADMIN_API_TOKEN", SecretStr("ops-token"))

    response = client.get("/api/ops/status", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"


def test_right_token_returns_the_report_uncached(client, monkeypatch, data_dir):
    monkeypatch.setattr(ops.config, "ADMIN_API_TOKEN", SecretStr("ops-token"))
    monkeypatch.setattr(ops.config, "MINIMAL_DATA_MODE", True)
    _ready_meta(data_dir / "f1.db")

    response = client.get("/api/ops/status", headers={"Authorization": "bearer ops-token"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    report = response.json()
    assert report["status"] == "ready"
    assert report["minimal_data_mode"] is True
    assert report["cache"]["memory"] == {
        "bmp": {"size": 0, "max_size": 512},
        "variant": {"size": 0, "max_size": ops.config.BMP_VARIANT_CACHE_MAXSIZE},
    }
    assert "ops-token" not in response.text
    assert str(data_dir) not in response.text
    assert "PRIVATE_SENTINEL" not in response.text
    assert not _IPV4.search(response.text)


def test_token_guessing_is_rate_limited(monkeypatch, data_dir):
    from app.utils import rate_limit

    monkeypatch.setattr(ops, "config", ops_status.config)
    monkeypatch.setattr(rate_limit, "config", ops_status.config)
    monkeypatch.setattr(ops.config, "ADMIN_API_TOKEN", SecretStr("ops-token"))
    monkeypatch.setattr(ops.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(ops.config, "STATS_RATE_LIMIT_PER_MINUTE", 1)
    rate_limit._RATE_LIMIT_BUCKETS.clear()
    client = TestClient(app)

    assert client.get("/api/ops/status").status_code == 401
    assert client.get("/api/ops/status").status_code == 429
    rate_limit._RATE_LIMIT_BUCKETS.clear()


def test_endpoint_is_not_in_the_public_openapi_schema():
    assert "/api/ops/status" not in app.openapi()["paths"]


def test_read_shares_the_sidecars_of_a_running_service(data_dir):
    db_path = data_dir / "f1.db"
    _ready_meta(db_path)
    with closing(sqlite3.connect(db_path)) as service:
        service.execute("PRAGMA journal_mode=WAL")
        service.execute(
            "INSERT OR REPLACE INTO cache_meta VALUES ('last_generation_status', 'degraded', 'x')"
        )
        service.commit()
        assert Path(f"{db_path}-wal").exists()

        report = ops_status.collect_ops_status(now=NOW)

    # The uncheckpointed write in the WAL is visible to the diagnostic read.
    assert report["generation"]["state"] == "degraded"


def test_module_runs_as_a_script(data_dir, monkeypatch, capsys):
    import runpy
    import sys

    # The re-executed module imports app.config.config; keep it the patched object.
    monkeypatch.setattr("app.config.config", ops_status.config)
    monkeypatch.setattr(sys, "argv", ["ops", "status", "--json"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(ops_status.__file__, run_name="__main__")

    assert exit_info.value.code == 2
    assert json.loads(capsys.readouterr().out)["reasons"] == ["generation_starting"]


def test_failed_first_external_backup_is_a_reason(data_dir, monkeypatch):
    _ready_meta(data_dir / "f1.db")
    _meta(data_dir / "f1.db", last_backup_failure=_iso(timedelta(hours=1)))
    monkeypatch.setattr("app.services.backup.is_backup_configured", lambda: True)

    assert ops_status.collect_ops_status(now=NOW)["reasons"] == ["backup_failed"]
