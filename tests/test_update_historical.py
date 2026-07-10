"""Tests for historical result refresh change detection."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.services import historical_refresh as update_historical
from app.utils.material_diff import has_material_change


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


def test_older_historical_results_never_replace_newer_stored_season():
    existing = _historical_payload("2026-06-01")
    older = {**_historical_payload("2025-06-01"), "season": 2025}

    assert update_historical._would_regress_season(older, existing)


class _MockHistoricalResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload

    @staticmethod
    def raise_for_status():
        return None


class _MockHistoricalClient:
    def __init__(self, *, include_bad_positions: bool = False):
        self.include_bad_positions = include_bad_positions

    async def get(self, url: str):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        limit = int(query.get("limit", ["0"])[0])

        if parsed.path.endswith("/qualifying.json"):
            if limit <= 3:
                qualifying = [
                    _qualifying_result(1, "RUS", "Russell", "Mercedes", "1:06.113"),
                    _qualifying_result(4, "ANT", "Antonelli", "Mercedes", "1:06.414"),
                    _qualifying_result(7, "PIA", "Piastri", "McLaren", "1:06.511"),
                ]
            else:
                qualifying = [
                    _qualifying_result(1, "RUS", "Russell", "Mercedes", "1:06.113"),
                    _qualifying_result(2, "LEC", "Leclerc", "Ferrari", "1:06.349"),
                    _qualifying_result(3, "HAM", "Hamilton", "Ferrari", "1:06.408"),
                    _qualifying_result(4, "ANT", "Antonelli", "Mercedes", "1:06.414"),
                ]
                if self.include_bad_positions:
                    qualifying.insert(
                        0,
                        _qualifying_result("NC", "BAD", "Badpos", "Test", "1:99.999"),
                    )

            return _MockHistoricalResponse(
                {"MRData": {"RaceTable": {"Races": [{"QualifyingResults": qualifying}]}}}
            )

        if limit <= 3:
            race = [
                _race_result(1, "RUS", "Russell", "Mercedes", "1:26:37.979"),
                _race_result(4, "ANT", "Antonelli", "Mercedes", "+1.986"),
                _race_result(7, "PIA", "Piastri", "McLaren", "+3.012"),
            ]
        else:
            race = [
                _race_result(1, "RUS", "Russell", "Mercedes", "1:26:37.979"),
                _race_result(2, "VER", "Verstappen", "Red Bull", "+1.611"),
                _race_result(3, "ANT", "Antonelli", "Mercedes", "+1.986"),
                _race_result(4, "PIA", "Piastri", "McLaren", "+3.012"),
            ]
            if self.include_bad_positions:
                race.insert(0, _race_result("NC", "BAD", "Badpos", "Test", "+9 laps"))

        return _MockHistoricalResponse({"MRData": {"RaceTable": {"Races": [{"Results": race}]}}})


class _MalformedCurrentYearClient(_MockHistoricalClient):
    async def get(self, url: str):
        if "/2026/" in url and urlparse(url).path.endswith("/qualifying.json"):
            malformed = [
                _qualifying_result(1, "RUS", "Russell", "Mercedes", "1:06.113"),
                _qualifying_result(2, "LEC", "Leclerc", "Ferrari", "1:06.349"),
                _qualifying_result(3, "HAM", "Hamilton", "Ferrari", "1:06.408"),
            ]
            malformed[0].pop("Driver")
            return _MockHistoricalResponse(
                {"MRData": {"RaceTable": {"Races": [{"QualifyingResults": malformed}]}}}
            )
        return await super().get(url)


def _qualifying_result(pos: int | str, code: str, name: str, team: str, q3_time: str) -> dict:
    return {
        "position": str(pos),
        "Driver": {"code": code, "familyName": name},
        "Constructor": {"name": team},
        "Q3": q3_time,
    }


def _race_result(pos: int | str, code: str, name: str, team: str, time: str) -> dict:
    return {
        "position": str(pos),
        "Driver": {"code": code, "familyName": name},
        "Constructor": {"name": team},
        "Time": {"time": time},
    }


@pytest.mark.asyncio
async def test_fetch_results_uses_actual_top_three_qualifying_positions(monkeypatch):
    monkeypatch.setattr(update_historical, "_current_year", lambda: 2026)

    results = await update_historical.fetch_results(
        _MockHistoricalClient(),
        "red_bull_ring",
    )

    assert results is not None
    assert [(entry["pos"], entry["code"]) for entry in results["qualifying"]] == [
        (1, "RUS"),
        (2, "LEC"),
        (3, "HAM"),
    ]


@pytest.mark.asyncio
async def test_fetch_results_uses_actual_top_three_race_positions(monkeypatch):
    monkeypatch.setattr(update_historical, "_current_year", lambda: 2026)

    results = await update_historical.fetch_results(
        _MockHistoricalClient(),
        "red_bull_ring",
    )

    assert results is not None
    assert [(entry["pos"], entry["code"]) for entry in results["race"]] == [
        (1, "RUS"),
        (2, "VER"),
        (3, "ANT"),
    ]


@pytest.mark.asyncio
async def test_fetch_results_ignores_non_numeric_positions(monkeypatch):
    monkeypatch.setattr(update_historical, "_current_year", lambda: 2026)

    results = await update_historical.fetch_results(
        _MockHistoricalClient(include_bad_positions=True),
        "red_bull_ring",
    )

    assert results is not None
    assert [(entry["pos"], entry["code"]) for entry in results["qualifying"]] == [
        (1, "RUS"),
        (2, "LEC"),
        (3, "HAM"),
    ]
    assert [(entry["pos"], entry["code"]) for entry in results["race"]] == [
        (1, "RUS"),
        (2, "VER"),
        (3, "ANT"),
    ]


@pytest.mark.asyncio
async def test_fetch_results_falls_back_when_current_year_contains_malformed_rows(monkeypatch):
    monkeypatch.setattr(update_historical, "_current_year", lambda: 2026)

    results = await update_historical.fetch_results(
        _MalformedCurrentYearClient(),
        "red_bull_ring",
    )

    assert results is not None
    assert results["season"] == 2025
    assert all(entry["code"] for entry in results["qualifying"])


def test_write_json_atomic_replaces_target(tmp_path, monkeypatch):
    target = tmp_path / "circuits_data.json"
    target.write_text('{"old": true}', encoding="utf-8")
    replace_calls = []
    from app.utils import atomic_io

    real_replace = atomic_io.os.replace

    def fake_replace(source, destination):
        replace_calls.append((source, destination))
        assert source.parent == target.parent
        assert source.name.startswith(f".{target.name}.")
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", fake_replace)

    atomic_io.atomic_write_json(target, {"new": True})

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == target
    assert target.read_text(encoding="utf-8") == '{\n  "new": true\n}\n'


@pytest.mark.asyncio
async def test_fetch_status_marks_systemic_upstream_failure_incomplete(monkeypatch):
    async def fail_fetch(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(update_historical, "fetch_with_retry", fail_fetch)
    monkeypatch.setattr(update_historical, "_current_year", lambda: 2027)

    outcome = await update_historical._fetch_results_with_status(object(), "monza")

    assert outcome.results is None
    assert outcome.completed is False
