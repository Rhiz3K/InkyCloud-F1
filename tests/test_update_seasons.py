"""Tests for preserving cancelled races during season refreshes."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import update_seasons


def _race(
    *,
    season: str,
    race_name: str,
    circuit_id: str,
    date: str,
    round_value: str | None = None,
) -> dict:
    race = {
        "season": season,
        "raceName": race_name,
        "Circuit": {
            "circuitId": circuit_id,
            "circuitName": f"{race_name} Circuit",
            "Location": {
                "locality": circuit_id.title(),
                "country": "Testland",
            },
        },
        "date": date,
        "time": "12:00:00Z",
    }
    if round_value is not None:
        race["round"] = round_value
    return race


def test_preserve_cancelled_races_keeps_roundless_entries_from_existing_file():
    fetched = {
        "season": "2026",
        "generated_at": "2026-03-24T00:00:00+00:00",
        "total_races": 2,
        "races": [
            _race(
                season="2026",
                round_value="1",
                race_name="Australian Grand Prix",
                circuit_id="albert_park",
                date="2026-03-08",
            ),
            _race(
                season="2026",
                round_value="2",
                race_name="Chinese Grand Prix",
                circuit_id="shanghai",
                date="2026-03-15",
            ),
        ],
    }
    existing = {
        "season": "2026",
        "generated_at": "2026-03-16T11:01:15+00:00",
        "total_races": 4,
        "races": [
            _race(
                season="2026",
                round_value="1",
                race_name="Australian Grand Prix",
                circuit_id="albert_park",
                date="2026-03-08",
            ),
            _race(
                season="2026",
                race_name="Bahrain Grand Prix",
                circuit_id="bahrain",
                date="2026-04-12",
            ),
            _race(
                season="2026",
                race_name="Saudi Arabian Grand Prix",
                circuit_id="jeddah",
                date="2026-04-19",
            ),
        ],
    }

    merged = update_seasons.preserve_cancelled_races(fetched, existing)

    assert merged["total_races"] == 4
    assert [race["Circuit"]["circuitId"] for race in merged["races"][-2:]] == [
        "bahrain",
        "jeddah",
    ]
    assert all("round" not in race for race in merged["races"][-2:])


def test_preserve_cancelled_races_skips_duplicates_when_api_restores_race():
    fetched = {
        "season": "2026",
        "generated_at": "2026-03-24T00:00:00+00:00",
        "total_races": 2,
        "races": [
            _race(
                season="2026",
                round_value="1",
                race_name="Australian Grand Prix",
                circuit_id="albert_park",
                date="2026-03-08",
            ),
            _race(
                season="2026",
                round_value="3",
                race_name="Bahrain Grand Prix",
                circuit_id="bahrain",
                date="2026-04-12",
            ),
        ],
    }
    existing = {
        "season": "2026",
        "generated_at": "2026-03-16T11:01:15+00:00",
        "total_races": 2,
        "races": [
            _race(
                season="2026",
                race_name="Bahrain Grand Prix",
                circuit_id="bahrain",
                date="2026-04-12",
            ),
        ],
    }

    merged = update_seasons.preserve_cancelled_races(fetched, existing)

    assert merged["total_races"] == 2
    assert [race["Circuit"]["circuitId"] for race in merged["races"]] == [
        "albert_park",
        "bahrain",
    ]
    assert merged["races"][1]["round"] == "3"


def test_write_season_file_appends_trailing_newline(tmp_path):
    output_path = tmp_path / "2027.json"

    update_seasons.write_season_file(
        output_path,
        {
            "season": "2027",
            "generated_at": "2026-03-25T00:00:00+00:00",
            "total_races": 0,
            "races": [],
        },
    )

    assert output_path.read_bytes().endswith(b"\n")


def test_season_change_detection_ignores_generated_at_only_changes():
    existing = {
        "season": "2026",
        "generated_at": "2026-03-24T00:00:00+00:00",
        "total_races": 1,
        "races": [
            _race(
                season="2026",
                round_value="1",
                race_name="Australian Grand Prix",
                circuit_id="albert_park",
                date="2026-03-08",
            )
        ],
    }
    refreshed = {
        **existing,
        "generated_at": "2026-04-06T00:00:00+00:00",
    }

    assert not update_seasons.has_material_season_change(refreshed, existing)


def test_season_change_detection_detects_calendar_changes():
    existing = {
        "season": "2026",
        "generated_at": "2026-03-24T00:00:00+00:00",
        "total_races": 1,
        "races": [
            _race(
                season="2026",
                round_value="1",
                race_name="Australian Grand Prix",
                circuit_id="albert_park",
                date="2026-03-08",
            )
        ],
    }
    refreshed = {
        **existing,
        "generated_at": "2026-04-06T00:00:00+00:00",
        "total_races": 2,
        "races": [
            *existing["races"],
            _race(
                season="2026",
                round_value="2",
                race_name="Chinese Grand Prix",
                circuit_id="shanghai",
                date="2026-03-15",
            ),
        ],
    }

    assert update_seasons.has_material_season_change(refreshed, existing)


def test_static_2026_calendar_keeps_cancelled_bahrain_and_jeddah():
    season_path = Path(__file__).resolve().parents[1] / "app" / "assets" / "seasons" / "2026.json"
    season_data = json.loads(season_path.read_text(encoding="utf-8"))

    cancelled_circuits = {
        race["Circuit"]["circuitId"]
        for race in season_data["races"]
        if race.get("round") in (None, "")
    }

    assert {"bahrain", "jeddah"}.issubset(cancelled_circuits)
