"""Refresh bundled circuit history from Jolpica without accepting partial rows."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.utils.atomic_io import atomic_write_json
from app.utils.http import fetch_with_retry
from app.utils.material_diff import has_material_change
from app.utils.result_entries import ResultEntry, get_result_mapping, sort_entries_by_position

API_BASE = "https://api.jolpi.ca/ergast/f1"
CIRCUITS_PATH = Path(__file__).resolve().parents[1] / "assets" / "circuits_data.json"
CURRENT_YEAR = datetime.now(timezone.utc).year
PODIUM_SIZE = 3
logger = logging.getLogger(__name__)


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


async def fetch_results(client: httpx.AsyncClient, circuit_id: str) -> dict | None:
    """Fetch the newest complete qualifying and race podium for a circuit."""
    for year in (CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2):
        try:
            qualifying_response = await fetch_with_retry(
                client,
                f"{API_BASE}/{year}/circuits/{circuit_id}/qualifying.json?limit=100",
                logger=logger,
            )
            qualifying_races = (
                qualifying_response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )
            if not qualifying_races:
                continue

            race_response = await fetch_with_retry(
                client,
                f"{API_BASE}/{year}/circuits/{circuit_id}/results.json?limit=100",
                logger=logger,
            )
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

            return {
                "season": year,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "qualifying": [
                    _format_qualifying_result(position, entry) for position, entry in qualifying
                ],
                "race": [_format_race_result(position, entry) for position, entry in race],
            }
        except httpx.HTTPStatusError:
            continue
        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
            print(f"  Error fetching {circuit_id}/{year}: {exc}")
            continue

    return None


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


async def main(circuit_filter: str | None = None) -> tuple[str, ...]:
    """Update historical data and return the circuit IDs that materially changed."""
    with open(CIRCUITS_PATH, encoding="utf-8") as handle:
        circuits = json.load(handle)

    if circuit_filter:
        circuit_ids = [circuit_filter] if circuit_filter in circuits else []
        if not circuit_ids:
            print(f"Circuit '{circuit_filter}' not found in circuits_data.json")
            return ()
    else:
        circuit_ids = list(circuits)

    print(f"Updating {len(circuit_ids)} circuits...")
    updated_circuits: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for circuit_id in circuit_ids:
            print(f"  {circuit_id}...", end=" ", flush=True)
            results = await fetch_results(client, circuit_id)
            if results:
                existing_historical = circuits[circuit_id].get("historical")
                if _would_regress_season(results, existing_historical):
                    print(
                        f"Kept newer stored season ({existing_historical.get('season')})"
                    )
                elif has_material_historical_change(results, existing_historical):
                    circuits[circuit_id]["historical"] = results
                    updated_circuits.append(circuit_id)
                    print(f"Updated ({results['season']})")
                else:
                    print(f"Unchanged ({results['season']})")
            else:
                print("No complete data")
            await asyncio.sleep(2.5)

    if updated_circuits:
        atomic_write_json(CIRCUITS_PATH, circuits)
        print(f"Saved to {CIRCUITS_PATH}")
    else:
        print("\nNo material historical changes; keeping existing file")

    print(f"\nUpdated {len(updated_circuits)}/{len(circuit_ids)} circuits")
    return tuple(updated_circuits)
