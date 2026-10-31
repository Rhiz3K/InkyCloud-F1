"""Tests for persistent runtime circuit-data seeding."""

import json

import pytest

from app.services import circuit_data


def test_runtime_circuit_data_is_seeded_beside_database(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"monza": {}}\n', encoding="utf-8")
    database_path = tmp_path / "persistent" / "f1.db"
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundled)
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    runtime_path = circuit_data.ensure_runtime_circuits_data()

    assert runtime_path == database_path.parent / "circuits_jolpica.json"
    assert json.loads(runtime_path.read_text()) == json.loads(bundled.read_text())
    assert circuit_data.get_circuits_data_path() == runtime_path


def test_existing_runtime_circuit_data_is_not_reseeded(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    bundled.write_text('{"seed": true}\n', encoding="utf-8")
    database_path = tmp_path / "persistent" / "f1.db"
    runtime_path = database_path.parent / "circuits_jolpica.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text('{"updated": true}\n', encoding="utf-8")
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundled)
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    assert circuit_data.ensure_runtime_circuits_data() == runtime_path
    assert runtime_path.read_text(encoding="utf-8") == '{"updated": true}\n'


def test_runtime_circuit_data_reload_tracks_atomic_file_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", tmp_path / "wikipedia.json")
    database_path = tmp_path / "persistent" / "f1.db"
    runtime_path = database_path.parent / "circuits_jolpica.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text('{"monza": {"historical": {"season": 2030}}}\n', encoding="utf-8")
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(database_path))

    first = circuit_data.load_circuits_data()
    runtime_path.write_text(
        '{"monza": {"historical": {"season": 2031}}, "spa": {}}\n',
        encoding="utf-8",
    )

    second = circuit_data.load_circuits_data()

    assert first["monza"]["historical"]["season"] == 2030
    assert second["monza"]["historical"]["season"] == 2031
    assert "spa" in second


def test_load_circuit_data_returns_empty_mapping_for_invalid_json(tmp_path, monkeypatch, caplog):
    """A corrupt runtime snapshot must not prevent the application from starting."""
    corrupt = tmp_path / "circuits_jolpica.json"
    corrupt.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(circuit_data, "get_circuits_data_path", lambda: corrupt)
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", corrupt)

    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", corrupt)
    assert circuit_data.load_circuits_data() == {}
    assert "Failed to load circuit data" in caplog.text


@pytest.mark.parametrize("stored_season", [2024, 2025, "2026", "invalid", None])
def test_upgrade_merges_metadata_and_preserves_newest_history(tmp_path, monkeypatch, stored_season):
    bundled = tmp_path / "bundled.json"
    runtime = tmp_path / "runtime.json"
    bundled.write_text(
        json.dumps(
            {
                "madring": {"number_of_laps": 57},
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
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundled)
    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", tmp_path / "wikipedia.json")
    result = circuit_data.load_circuits_data(runtime)
    assert "circuit_length" not in result["madring"]
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
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundled)
    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", tmp_path / "wikipedia.json")
    assert circuit_data.load_circuits_data(runtime)["madring"]["number_of_laps"] == 57


def test_invalid_bundled_circuit_does_not_replace_runtime_record(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.json"
    runtime = tmp_path / "runtime.json"
    bundled.write_text(json.dumps({"monza": None}))
    runtime.write_text(json.dumps({"monza": {"name": "Monza"}}))
    monkeypatch.setattr("app.services.circuit_data.BUNDLED_JOLPICA_PATH", bundled)
    assert circuit_data.load_jolpica_data(runtime)["monza"] == {}


@pytest.mark.parametrize("season", [2024, 2025, 2026])
def test_migration_copies_only_newest_history(tmp_path, monkeypatch, caplog, season):
    """Legacy metadata and provenance never enter the new mutable file."""
    bundled = tmp_path / "bundle.json"
    bundled.write_text(
        json.dumps({"a": {"historical": {"season": 2025}, "_provenance": {"license": "Jolpica"}}})
    )
    legacy = tmp_path / "circuits_data.json"
    legacy.write_text(
        json.dumps(
            {
                "a": {
                    "historical": {"season": season, "runtime": True},
                    "circuit_length": "bad",
                    "_provenance": {"old": True},
                },
                "retired": {"historical": {"season": 2000}, "url_slug": "bad"},
            }
        )
    )
    original = legacy.read_bytes()
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundled)
    monkeypatch.setattr(circuit_data.config, "DATABASE_PATH", str(tmp_path / "db"))
    assert circuit_data.get_circuits_data_path() == bundled
    with caplog.at_level("INFO"):
        result = json.loads(circuit_data.ensure_runtime_circuits_data().read_text())
    assert result["a"] == {
        "historical": {"season": season, "runtime": True} if season >= 2025 else {"season": 2025},
        "_provenance": {"license": "Jolpica"},
    }
    assert result["retired"] == {"historical": {"season": 2000}}
    assert legacy.read_bytes() == original
    assert caplog.text.count("Migrated legacy circuit history") == 1


def test_wikipedia_overlay_and_credits(tmp_path, monkeypatch):
    """Only text facts overlay Jolpica; attribution is isolated and revisions deduplicated."""
    bundle, wiki, runtime = (tmp_path / name for name in ("bundle", "wiki", "runtime"))
    bundle.write_text(
        json.dumps({"a": {"number_of_laps": 50, "_provenance": {"source": "jolpica"}}, "plain": {}})
    )
    runtime.write_text(
        json.dumps({"a": {"circuit_length": "bad", "_provenance": {"bad": True}}, "invalid": None})
    )
    field = {
        "title": "Circuit / A",
        "revid": 123,
        "source": "https://en.wikipedia.org/w/index.php?title=Circuit_A&oldid=123",
        "retrieved_on": "2026-10-31",
    }
    wiki.write_text(
        json.dumps(
            {
                "a": {
                    "circuit_length": "5 km",
                    "fastest_lap_year": 2025,
                    "race_name": "bad",
                    "_provenance": {
                        "fields": {
                            "circuit_length": field,
                            "duplicate": field,
                            "invalid": None,
                            "bad_url": {**field, "source": "https://example.com"},
                            "bad_id": {**field, "revid": "123"},
                            "bad_title": {**field, "title": None},
                            "bad_type": {**field, "source": None},
                            "bool_id": {**field, "revid": True},
                        }
                    },
                },
                "wiki_only": {"fastest_lap_time": "1:20", "_provenance": None},
                "invalid": None,
                "bad_fields": {"_provenance": {"fields": []}},
            }
        )
    )
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", bundle)
    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", wiki)
    result = circuit_data.load_circuits_data(runtime)
    assert result["a"]["circuit_length"] == "5 km"
    assert "fastest_lap_year" not in result["a"] and "race_name" not in result["a"]
    assert result["a"]["_provenance"]["jolpica"] == {"source": "jolpica"}
    assert result["wiki_only"] == {"fastest_lap_time": "1:20", "_provenance": {"wikipedia": None}}
    assert "_provenance" not in result["plain"]
    result["a"]["_provenance"]["wikipedia"]["fields"].clear()
    assert circuit_data.wikipedia_circuit_credits() == [
        {
            "title": "Circuit / A",
            "revid": 123,
            "url": field["source"],
            "history_url": "https://en.wikipedia.org/w/index.php?title=Circuit%20%2F%20A&action=history",
        }
    ]
    assert "circuit_length" not in circuit_data.load_jolpica_data(runtime)["a"]


@pytest.mark.parametrize("payload", ["[]", "null"])
def test_non_mapping_snapshot_is_empty(tmp_path, monkeypatch, payload):
    """Unexpected JSON roots cannot break startup or leak facts."""
    file = tmp_path / "data"
    file.write_text(payload)
    monkeypatch.setattr(circuit_data, "BUNDLED_JOLPICA_PATH", file)
    monkeypatch.setattr(circuit_data, "BUNDLED_WIKIPEDIA_PATH", file)
    assert circuit_data.load_circuits_data(file) == {}


def test_credits_route_renders_with_wikipedia_context(monkeypatch):
    """The public credits page receives the revision list and still renders HTML."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routes import credits

    expected = [
        {
            "title": "Circuit A",
            "revid": 1,
            "url": "https://example.com",
            "history_url": "https://example.com/history",
        }
    ]
    monkeypatch.setattr(credits, "wikipedia_circuit_credits", lambda: expected)
    render = credits.templates.TemplateResponse
    contexts = []

    def capture(request, name, context):
        """Capture context while exercising the real template and response."""
        contexts.append(context)
        return render(request, name, context)

    monkeypatch.setattr(credits.templates, "TemplateResponse", capture)
    response = TestClient(app).get("/credits")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert contexts[0]["circuit_wiki_credits"] == expected


def test_wikipedia_credits_sort_titles_and_keep_distinct_revisions(monkeypatch):
    """Different revisions remain creditable while repeated field sources collapse."""

    def revision(title, revid):
        """Construct one pinned per-field attribution."""
        return {
            "title": title,
            "revid": revid,
            "source": f"https://en.wikipedia.org/w/index.php?title={title}&oldid={revid}",
        }

    fields = {"length": revision("Z", 1), "time": revision("A", 2)}
    other_fields = {"driver": revision("A", 1), "year": revision("A", 2)}
    monkeypatch.setattr(
        circuit_data,
        "load_wikipedia_data",
        lambda: {
            "one": {"_provenance": {"fields": fields}},
            "two": {"_provenance": {"fields": other_fields}},
        },
    )
    assert [
        (credit["title"], credit["revid"]) for credit in circuit_data.wikipedia_circuit_credits()
    ] == [("A", 1), ("A", 2), ("Z", 1)]
