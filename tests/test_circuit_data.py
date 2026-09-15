"""Tests for persistent runtime circuit-data seeding."""

import json

import pytest

from app.services import circuit_data


def test_runtime_circuit_data_is_seeded_beside_database(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"monza": {}}\n', encoding="utf-8")
    database_path = tmp_path / "persistent" / "f1.db"
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", bundled)
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    runtime_path = circuit_data.ensure_runtime_circuits_data()

    assert runtime_path == database_path.parent / "circuits_data.json"
    assert runtime_path.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")
    assert circuit_data.get_circuits_data_path() == runtime_path


def test_existing_runtime_circuit_data_is_not_reseeded(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"seed": true}\n', encoding="utf-8")
    database_path = tmp_path / "persistent" / "f1.db"
    runtime_path = database_path.parent / "circuits_data.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text('{"updated": true}\n', encoding="utf-8")
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", bundled)
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    assert circuit_data.ensure_runtime_circuits_data() == runtime_path
    assert runtime_path.read_text(encoding="utf-8") == '{"updated": true}\n'


def test_runtime_circuit_data_reload_tracks_atomic_file_versions(tmp_path, monkeypatch):
    database_path = tmp_path / "persistent" / "f1.db"
    runtime_path = database_path.parent / "circuits_data.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text('{"monza": {"length": "5.7 km"}}\n', encoding="utf-8")
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    first = circuit_data.load_circuits_data()
    runtime_path.write_text(
        '{"monza": {"length": "5.8 km"}, "spa": {}}\n',
        encoding="utf-8",
    )

    second = circuit_data.load_circuits_data()

    assert first["monza"]["length"] == "5.7 km"
    assert second["monza"]["length"] == "5.8 km"
    assert "spa" in second


def test_load_circuit_data_returns_empty_mapping_for_invalid_json(tmp_path, monkeypatch, caplog):
    """A corrupt runtime snapshot must not prevent the application from starting."""
    corrupt = tmp_path / "circuits_data.json"
    corrupt.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(circuit_data, "get_circuits_data_path", lambda: corrupt)
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", corrupt)

    assert circuit_data.load_circuits_data() == {}
    assert "Failed to load circuit data" in caplog.text


@pytest.mark.parametrize("stored_season", [2024, 2025, "2026", "invalid", None])
def test_upgrade_merges_metadata_and_preserves_newest_history(tmp_path, monkeypatch, stored_season):
    bundled = tmp_path / "bundled.json"
    runtime = tmp_path / "runtime.json"
    bundled.write_text(
        json.dumps(
            {
                "madring": {"circuit_length": "5.416km", "number_of_laps": 57},
                "monza": {"historical": {"season": 2025, "source": "bundle"}},
                "new_track": {},
            }
        )
    )
    stored_history = {"season": stored_season, "source": "runtime"}
    runtime.write_text(
        json.dumps(
            {
                "madring": {"circuit_length": "5.474km", "number_of_laps": None},
                "monza": {"historical": stored_history},
                "retired_track": {"historical": {"season": 2020}},
            }
        )
    )
    previous_bytes = runtime.read_bytes()
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", bundled)
    result = circuit_data.load_circuits_data(runtime)
    assert result["madring"]["circuit_length"] == "5.416km"
    assert result["madring"]["number_of_laps"] == 57
    assert "new_track" in result and "retired_track" in result
    assert result["monza"]["historical"]["source"] == (
        "runtime" if stored_season in (2025, "2026") else "bundle"
    )
    assert runtime.read_bytes() == previous_bytes
    result["monza"]["historical"]["source"] = "mutated"
    assert circuit_data.load_circuits_data(runtime)["monza"]["historical"]["source"] != "mutated"


def test_corrupt_runtime_uses_bundled_metadata(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"madring": {"number_of_laps": 57}}')
    runtime = tmp_path / "runtime.json"
    runtime.write_text("corrupt")
    monkeypatch.setattr(circuit_data, "BUNDLED_CIRCUITS_DATA_PATH", bundled)
    assert circuit_data.load_circuits_data(runtime)["madring"]["number_of_laps"] == 57


def test_invalid_bundled_circuit_does_not_replace_runtime_record(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    runtime = tmp_path / "runtime.json"
    bundled.write_text(json.dumps({"monza": None}))
    runtime.write_text(json.dumps({"monza": {"name": "Monza"}}))
    monkeypatch.setattr("app.services.circuit_data.BUNDLED_CIRCUITS_DATA_PATH", bundled)
    assert circuit_data.load_circuits_data(runtime)["monza"]["name"] == "Monza"
