"""Ensure runtime diagnostics read live WAL counts without exposing or altering records."""

import json
import runpy
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from app.utils import runtime_audit


@pytest.fixture
def live_db(tmp_path):
    path = tmp_path / "live.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        for table in runtime_audit.USAGE_TABLES:
            db.execute(f"CREATE TABLE {table} (private_value TEXT)")
        db.execute("CREATE TABLE weather_api_requests (timestamp REAL)")
        db.commit()
        yield path, db


def test_counts_include_wal_without_reading_private_values_or_changing_files(live_db):
    path, db = live_db
    now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    db.execute("INSERT INTO api_calls VALUES ('PRIVATE_SENTINEL')")
    db.execute("INSERT INTO api_call_totals VALUES ('PRIVATE_SENTINEL')")
    db.executemany(
        "INSERT INTO weather_api_requests VALUES (?)",
        [(now.timestamp() - age,) for age in (0, 60, 3600, 86400, 31 * 86400)],
    )
    db.commit()
    wal = path.with_name(path.name + "-wal")
    before = (path.read_bytes(), wal.read_bytes())
    report = runtime_audit.audit_database(path, minimal_mode=False, aggregate_only=True, now=now)
    assert report["status"] == "needs_attention"
    assert report["row_counts"]["api_calls"] == report["row_counts"]["api_call_totals"] == 1
    assert report["weather_attempts"] == {"minute": 1, "hour": 2, "day": 3, "rolling_31_days": 4}
    assert len(report["findings"]) == 1 and "api_calls" in report["findings"][0]
    assert "PRIVATE_SENTINEL" not in json.dumps(report)
    assert (path.read_bytes(), wal.read_bytes()) == before


@pytest.mark.parametrize(
    "minimal,aggregate,attention", [(False, True, False), (True, True, True), (False, False, True)]
)
def test_cli_reports_profile_findings_without_personal_values(
    live_db, monkeypatch, capsys, minimal, aggregate, attention
):
    path, db = live_db
    db.execute("INSERT INTO perf_buckets VALUES ('PRIVATE_SENTINEL')")
    db.commit()
    monkeypatch.setattr(runtime_audit.config, "MINIMAL_DATA_MODE", minimal)
    monkeypatch.setattr(runtime_audit.config, "AGGREGATE_STATS_ONLY", aggregate)
    assert runtime_audit.main(["--database", str(path)]) == int(attention)
    output = capsys.readouterr().out
    report = json.loads(output)
    assert bool(report["findings"]) is attention
    assert "PRIVATE_SENTINEL" not in output


def test_missing_table_is_not_reported_as_empty(live_db):
    path, db = live_db
    db.execute("DROP TABLE api_calls")
    db.commit()
    report = runtime_audit.audit_database(path, minimal_mode=False, aggregate_only=True)
    assert report["row_counts"]["api_calls"] is None
    assert report["status"] == "needs_attention"


@pytest.mark.parametrize("exists", [False, True])
def test_unavailable_database_is_not_created_or_initialized(tmp_path, capsys, exists):
    path = tmp_path / "missing.db"
    if exists:
        path.write_bytes(b"not a SQLite database")
    assert runtime_audit.main(["--database", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
    assert path.exists() is exists
    if exists:
        assert path.read_bytes() == b"not a SQLite database"


def test_module_entrypoint_uses_configured_database(live_db, monkeypatch, capsys):
    path, _ = live_db
    monkeypatch.setattr(runtime_audit.config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(runtime_audit.config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(runtime_audit.config, "AGGREGATE_STATS_ONLY", True)
    # Other configuration tests reload app.config; pin the entrypoint's fresh import too.
    monkeypatch.setattr("app.config.config", runtime_audit.config)
    monkeypatch.setattr("sys.argv", ["runtime_audit"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(runtime_audit.__file__, run_name="__main__")
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
