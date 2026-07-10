"""Tests for persistent runtime circuit-data seeding."""

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
