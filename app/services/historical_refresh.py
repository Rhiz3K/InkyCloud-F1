"""Refresh bundled circuit history from Jolpica without accepting partial rows."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.circuit_data import ensure_runtime_circuits_data
from app.utils.atomic_io import atomic_write_json
from app.utils.http import fetch_with_retry
from app.utils.jolpica import get_jolpica_base_url
from app.utils.material_diff import has_material_change
from app.utils.result_entries import ResultEntry, get_result_mapping, sort_entries_by_position

PODIUM_SIZE = 3
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitFetchOutcome:
    results: dict | None
    completed: bool


@dataclass(frozen=True)
class HistoricalRefreshResult:
    updated_circuits: tuple[str, ...]
    failed_circuits: tuple[str, ...]
    attempted_circuits: int

    @property
    def completed(self) -> bool:
        return self.attempted_circuits > 0 and not self.failed_circuits


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def has_material_historical_change(results: dict, existing_historical: dict | None) -> bool:
    """Return whether historical results changed beyond the refresh timestamp."""
    return has_material_change(results, existing_historical, ignored_keys=("updated_at",))


def _required_text(mapping: Mapping[str, Any], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {context}.{key}")
    return value.strip()


def _format_result(
    position: int,
    entry: ResultEntry,
    *,
    time_value: object,
) -> dict[str, object]:
    driver = get_result_mapping(entry, "Driver")
    constructor = get_result_mapping(entry, "Constructor")
    if not isinstance(time_value, str) or not time_value.strip():
        raise ValueError(f"Missing result time for position {position}")

    return {
        "pos": position,
        "code": _required_text(driver, "code", context="Driver"),
        "name": _required_text(driver, "familyName", context="Driver"),
        "team": _required_text(constructor, "name", context="Constructor"),
        "time": time_value.strip(),
    }


def _format_qualifying_result(position: int, entry: ResultEntry) -> dict[str, object]:
    """Convert one complete qualifying row into stored historical JSON."""
    return _format_result(
        position,
        entry,
        time_value=entry.get("Q3") or entry.get("Q2") or entry.get("Q1"),
    )


def _format_race_result(position: int, entry: ResultEntry) -> dict[str, object]:
    """Convert one complete race row into stored historical JSON."""
    time_data = get_result_mapping(entry, "Time")
    return _format_result(position, entry, time_value=time_data.get("time"))


async def _fetch_results_with_status(
    client: httpx.AsyncClient, circuit_id: str
) -> CircuitFetchOutcome:
    """Fetch a podium and retain whether every upstream attempt completed cleanly."""
    current_year = _current_year()
    had_valid_response = False
    had_failure = False
    api_base = get_jolpica_base_url()
    for year in (current_year, current_year - 1, current_year - 2):
        try:
            qualifying_response = await fetch_with_retry(
                client,
                f"{api_base}/{year}/circuits/{circuit_id}/qualifying.json?limit=100",
                logger=logger,
            )
            had_valid_response = True
            qualifying_races = (
                qualifying_response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )
            if not qualifying_races:
                continue

            race_response = await fetch_with_retry(
                client,
                f"{api_base}/{year}/circuits/{circuit_id}/results.json?limit=100",
                logger=logger,
            )
            had_valid_response = True
            race_races = (
                race_response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )
            if not race_races:
                continue

            qualifying = sort_entries_by_position(
                qualifying_races[0].get("QualifyingResults")
            )[:PODIUM_SIZE]
            race = sort_entries_by_position(race_races[0].get("Results"))[:PODIUM_SIZE]
            if len(qualifying) != PODIUM_SIZE or len(race) != PODIUM_SIZE:
                raise ValueError("Incomplete podium results")

            return CircuitFetchOutcome(
                results={
                    "season": year,
                    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "qualifying": [
                        _format_qualifying_result(position, entry)
                        for position, entry in qualifying
                    ],
                    "race": [_format_race_result(position, entry) for position, entry in race],
                },
                completed=not had_failure,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                had_valid_response = True
            else:
                had_failure = True
                logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
            continue
        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
            had_failure = True
            logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
            continue

    return CircuitFetchOutcome(results=None, completed=had_valid_response and not had_failure)


async def fetch_results(client: httpx.AsyncClient, circuit_id: str) -> dict | None:
    """Fetch the newest complete qualifying and race podium for a circuit."""
    return (await _fetch_results_with_status(client, circuit_id)).results


def _would_regress_season(results: dict, existing_historical: object) -> bool:
    """Prevent a transient current-year failure from replacing newer stored results."""
    if not isinstance(existing_historical, dict):
        return False
    result_season = results.get("season")
    existing_season = existing_historical.get("season")
    if not isinstance(result_season, (str, int)) or not isinstance(
        existing_season, (str, int)
    ):
        return False
    try:
        return int(result_season) < int(existing_season)
    except (TypeError, ValueError):
        return False


async def main(circuit_filter: str | None = None) -> HistoricalRefreshResult:
    """Update persistent historical data and report completion separately from changes."""
    circuits_path = ensure_runtime_circuits_data()
    with open(circuits_path, encoding="utf-8") as handle:
        circuits = json.load(handle)

    if circuit_filter:
        circuit_ids = [circuit_filter] if circuit_filter in circuits else []
        if not circuit_ids:
            logger.error("Circuit %s not found in circuits_data.json", circuit_filter)
            return HistoricalRefreshResult((), (circuit_filter,), 0)
    else:
        circuit_ids = list(circuits)

    logger.info("Updating historical results for %s circuits", len(circuit_ids))
    updated_circuits: list[str] = []
    failed_circuits: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for circuit_id in circuit_ids:
            outcome = await _fetch_results_with_status(client, circuit_id)
            results = outcome.results
            if not outcome.completed:
                failed_circuits.append(circuit_id)
            if results:
                existing_historical = circuits[circuit_id].get("historical")
                if _would_regress_season(results, existing_historical):
                    logger.info(
                        "Kept newer stored season %s for %s",
                        existing_historical.get("season"),
                        circuit_id,
                    )
                elif has_material_historical_change(results, existing_historical):
                    circuits[circuit_id]["historical"] = results
                    updated_circuits.append(circuit_id)
                    logger.info(
                        "Updated %s historical results from %s", circuit_id, results["season"]
                    )
                else:
                    logger.debug("Historical results unchanged for %s", circuit_id)
            else:
                logger.info("No complete historical data for %s", circuit_id)
            await asyncio.sleep(2.5)

    if updated_circuits:
        atomic_write_json(circuits_path, circuits)
        logger.info("Saved historical results to %s", circuits_path)

    logger.info(
        "Historical refresh updated %s/%s circuits; %s failed",
        len(updated_circuits),
        len(circuit_ids),
        len(failed_circuits),
    )
    return HistoricalRefreshResult(
        tuple(updated_circuits), tuple(failed_circuits), len(circuit_ids)
    )
