"""Tests for preserving cancelled races during season refreshes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

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


def test_season_change_detection_detects_new_payload_fields():
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
        "source_revision": "jolpica-2026-04-06",
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


@pytest.mark.asyncio
async def test_fetch_season_rejects_empty_calendar():
    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {"MRData": {"RaceTable": {"Races": []}}}
    client = AsyncMock()
    client.get.return_value = response

    with pytest.raises(update_seasons.SeasonNotPublishedError, match="no races"):
        await update_seasons.fetch_season(client, 2027)


@pytest.mark.asyncio
async def test_fetch_season_rejects_malformed_race_rows():
    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {
        "MRData": {"RaceTable": {"Races": [{"date": "2027-03-01", "Circuit": {}}]}}
    }
    client = AsyncMock()
    client.get.return_value = response

    with pytest.raises(ValueError, match="malformed"):
        await update_seasons.fetch_season(client, 2027)


@pytest.mark.asyncio
async def test_main_returns_false_when_any_year_fails(tmp_path, monkeypatch):
    async def fake_fetch(_client, year):
        if year == 2027:
            raise RuntimeError("upstream unavailable")
        return {"season": str(year), "generated_at": "now", "total_races": 1, "races": [{}]}

    monkeypatch.setattr(update_seasons, "SEASONS_DIR", tmp_path)
    monkeypatch.setattr(update_seasons, "fetch_season", fake_fetch)
    monkeypatch.setattr(update_seasons.asyncio, "sleep", AsyncMock())

    assert await update_seasons.main([2026, 2027]) is False


@pytest.mark.asyncio
async def test_main_returns_true_when_all_years_succeed(tmp_path, monkeypatch):
    async def fake_fetch(_client, year):
        return {
            "season": str(year),
            "generated_at": "now",
            "total_races": 1,
            "races": [{"date": "2027-03-01", "Circuit": {"circuitId": "test"}}],
        }

    monkeypatch.setattr(update_seasons, "SEASONS_DIR", tmp_path)
    monkeypatch.setattr(update_seasons, "fetch_season", fake_fetch)
    monkeypatch.setattr(update_seasons.asyncio, "sleep", AsyncMock())

    assert await update_seasons.main([2027]) is True


@pytest.mark.asyncio
async def test_main_skips_unpublished_next_season_without_failing(tmp_path, monkeypatch, capsys):
    async def fake_fetch(_client, year):
        if year == 2027:
            raise update_seasons.SeasonNotPublishedError("Jolpica returned no races for 2027")
        return {
            "season": str(year),
            "generated_at": "now",
            "total_races": 1,
            "races": [{"date": "2026-03-01", "Circuit": {"circuitId": "test"}}],
        }

    (tmp_path / "2027.json").write_text(
        json.dumps({"season": "2027", "total_races": 0, "races": []}), encoding="utf-8"
    )
    monkeypatch.setattr(update_seasons, "SEASONS_DIR", tmp_path)
    monkeypatch.setattr(update_seasons, "fetch_season", fake_fetch)
    monkeypatch.setattr(update_seasons.asyncio, "sleep", AsyncMock())

    assert await update_seasons.main([2026, 2027]) is True
    assert (tmp_path / "2026.json").exists()
    assert "Season 2027 is not published yet" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_main_fails_when_existing_season_file_is_unreadable(tmp_path, monkeypatch, capsys):
    (tmp_path / "2027.json").write_text("{not json", encoding="utf-8")

    async def fake_fetch(_client, year):
        raise update_seasons.SeasonNotPublishedError(f"Jolpica returned no races for {year}")

    monkeypatch.setattr(update_seasons, "SEASONS_DIR", tmp_path)
    monkeypatch.setattr(update_seasons, "fetch_season", fake_fetch)
    monkeypatch.setattr(update_seasons.asyncio, "sleep", AsyncMock())

    assert await update_seasons.main([2027]) is False
    assert "could not be read" in capsys.readouterr().out
    assert (tmp_path / "2027.json").read_text(encoding="utf-8") == "{not json"


@pytest.mark.asyncio
async def test_main_fails_when_a_published_season_returns_no_races(tmp_path, monkeypatch):
    existing = {
        "season": "2026",
        "total_races": 1,
        "races": [{"round": "1", "date": "2026-03-01", "Circuit": {"circuitId": "test"}}],
    }
    (tmp_path / "2026.json").write_text(json.dumps(existing), encoding="utf-8")

    async def fake_fetch(_client, year):
        raise update_seasons.SeasonNotPublishedError(f"Jolpica returned no races for {year}")

    monkeypatch.setattr(update_seasons, "SEASONS_DIR", tmp_path)
    monkeypatch.setattr(update_seasons, "fetch_season", fake_fetch)
    monkeypatch.setattr(update_seasons.asyncio, "sleep", AsyncMock())

    assert await update_seasons.main([2026]) is False
    assert json.loads((tmp_path / "2026.json").read_text(encoding="utf-8")) == existing
