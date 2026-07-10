"""Tests for the one-shot circuit metadata scraper."""

import json

import pytest

from scripts.scrape_circuits import _load_existing_circuits, _merge_circuit_entry


def test_merge_circuit_entry_preserves_historical_results():
    historical = {"2025": [{"position": 1, "driver": "NOR"}]}

    merged = _merge_circuit_entry(
        {"historical": historical, "circuit_length": "old"},
        race_name="Australian Grand Prix",
        url_slug="australia",
        scraped={"circuit_length": "5.278 km"},
    )

    assert merged["historical"] == historical
    assert merged["circuit_length"] == "5.278 km"


def test_merge_circuit_entry_does_not_replace_valid_values_with_none():
    merged = _merge_circuit_entry(
        {"circuit_length": "5.278 km", "lap_record": "1:20.235"},
        race_name="Australian Grand Prix",
        url_slug="australia",
        scraped={"circuit_length": None, "lap_record": "1:19.813"},
    )

    assert merged["circuit_length"] == "5.278 km"
    assert merged["lap_record"] == "1:19.813"


def test_load_existing_circuits_refuses_invalid_json(tmp_path):
    output_path = tmp_path / "circuits_data.json"
    output_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cannot safely update"):
        _load_existing_circuits(output_path)


def test_load_existing_circuits_reads_object(tmp_path):
    output_path = tmp_path / "circuits_data.json"
    output_path.write_text(json.dumps({"monza": {"historical": {}}}), encoding="utf-8")

    assert _load_existing_circuits(output_path) == {"monza": {"historical": {}}}
