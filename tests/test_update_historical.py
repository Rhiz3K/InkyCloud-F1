"""Tests for historical result refresh change detection."""

from __future__ import annotations

from scripts import update_historical
from scripts.material_diff import has_material_change


def _historical_payload(
    updated_at: str, driver_code: str = "VER", driver_name: str = "Verstappen"
) -> dict:
    return {
        "season": 2026,
        "updated_at": updated_at,
        "qualifying": [
            {
                "pos": 1,
                "code": driver_code,
                "name": driver_name,
                "team": "Red Bull",
                "time": "1:20.000",
            }
        ],
        "race": [
            {
                "pos": 1,
                "code": driver_code,
                "name": driver_name,
                "team": "Red Bull",
                "time": "1:30:00.000",
            }
        ],
    }


def test_historical_change_detection_ignores_updated_at_only_changes():
    existing = _historical_payload("2026-03-30")
    refreshed = _historical_payload("2026-04-06")

    assert not update_historical.has_material_historical_change(refreshed, existing)


def test_historical_change_detection_detects_result_changes():
    existing = _historical_payload("2026-03-30")
    refreshed = _historical_payload("2026-04-06", driver_code="NOR", driver_name="Norris")

    assert update_historical.has_material_historical_change(refreshed, existing)


def test_material_change_detection_detects_new_payload_fields():
    existing = _historical_payload("2026-03-30")
    refreshed = {
        **_historical_payload("2026-04-06"),
        "fastest_lap": {"code": "VER", "time": "1:22.000"},
    }

    assert has_material_change(refreshed, existing, ignored_keys=("updated_at",))
