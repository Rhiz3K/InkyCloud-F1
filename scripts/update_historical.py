#!/usr/bin/env python3
"""
Update historical race results in circuits_data.json.

Usage:
    python scripts/update_historical.py [--circuit albert_park]

This script is meant to be run:
- After each Grand Prix (Monday after race)
- Via GitHub Action weekly
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.material_diff import has_material_change

API_BASE = "https://api.jolpi.ca/ergast/f1"
CIRCUITS_PATH = Path(__file__).parent.parent / "app" / "assets" / "circuits_data.json"
CURRENT_YEAR = datetime.now().year


def has_material_historical_change(results: dict, existing_historical: dict | None) -> bool:
    """Return True when historical results changed beyond updated_at metadata."""
    return has_material_change(results, existing_historical, ignored_keys=("updated_at",))


def _parse_result_position(entry: object) -> int | None:
    if not isinstance(entry, dict):
        return None

    try:
        return int(entry.get("position"))
    except (TypeError, ValueError):
        return None


def _sort_entries_by_position(entries: object) -> list[tuple[int, dict]]:
    if not isinstance(entries, list):
        return []

    positioned_entries = []
    for entry in entries:
        position = _parse_result_position(entry)
        if position is not None and isinstance(entry, dict):
            positioned_entries.append((position, entry))

    return sorted(positioned_entries, key=lambda item: item[0])


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


async def fetch_results(client: httpx.AsyncClient, circuit_id: str) -> dict | None:
    """Fetch latest qualifying and race results for a circuit."""
    for year in [CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2]:
        try:
            # Fetch qualifying
            q_url = f"{API_BASE}/{year}/circuits/{circuit_id}/qualifying.json?limit=100"
            q_resp = await client.get(q_url)
            q_resp.raise_for_status()
            q_data = q_resp.json()
            q_races = q_data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not q_races:
                continue

            # Fetch race results
            r_url = f"{API_BASE}/{year}/circuits/{circuit_id}/results.json?limit=100"
            r_resp = await client.get(r_url)
            r_resp.raise_for_status()
            r_data = r_resp.json()
            r_races = r_data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not r_races:
                continue

            # Parse qualifying
            qualifying = []
            qualifying_results = _sort_entries_by_position(q_races[0].get("QualifyingResults"))
            for position, q in qualifying_results[:3]:
                driver = q.get("Driver") or {}
                constructor = q.get("Constructor") or {}
                qualifying.append(
                    {
                        "pos": position,
                        "code": driver.get("code", ""),
                        "name": driver.get("familyName", ""),
                        "team": constructor.get("name", ""),
                        "time": q.get("Q3") or q.get("Q2") or q.get("Q1"),
                    }
                )

            # Parse race
            race = []
            race_results = _sort_entries_by_position(r_races[0].get("Results"))
            for position, r in race_results[:3]:
                driver = r.get("Driver") or {}
                constructor = r.get("Constructor") or {}
                time_data = r.get("Time") or {}
                race.append(
                    {
                        "pos": position,
                        "code": driver.get("code", ""),
                        "name": driver.get("familyName", ""),
                        "team": constructor.get("name", ""),
                        "time": time_data.get("time"),
                    }
                )

            return {
                "season": year,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "qualifying": qualifying,
                "race": race,
            }

        except httpx.HTTPStatusError:
            continue
        except Exception as e:
            print(f"  Error fetching {circuit_id}/{year}: {e}")
            continue

    return None


async def main(circuit_filter: str | None = None) -> int:
    """Update historical data for circuits."""
    with open(CIRCUITS_PATH, encoding="utf-8") as f:
        circuits = json.load(f)

    # Determine which circuits to update
    if circuit_filter:
        circuit_ids = [circuit_filter] if circuit_filter in circuits else []
        if not circuit_ids:
            print(f"Circuit '{circuit_filter}' not found in circuits_data.json")
            return 0
    else:
        circuit_ids = list(circuits.keys())

    print(f"Updating {len(circuit_ids)} circuits...")

    updated_count = 0
    has_changes = False
    async with httpx.AsyncClient(timeout=30) as client:
        for circuit_id in circuit_ids:
            print(f"  {circuit_id}...", end=" ", flush=True)

            results = await fetch_results(client, circuit_id)
            if results:
                existing_historical = circuits[circuit_id].get("historical")
                if has_material_historical_change(results, existing_historical):
                    circuits[circuit_id]["historical"] = results
                    print(f"Updated ({results['season']})")
                    updated_count += 1
                    has_changes = True
                else:
                    print(f"Unchanged ({results['season']})")
            else:
                print("No data")

            # Rate limiting
            await asyncio.sleep(2.5)

    if has_changes:
        _write_json_atomic(CIRCUITS_PATH, circuits)
    else:
        print("\nNo material historical changes; keeping existing file")

    print(f"\nUpdated {updated_count}/{len(circuit_ids)} circuits")
    if has_changes:
        print(f"Saved to {CIRCUITS_PATH}")
    return updated_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update historical race results")
    parser.add_argument(
        "--circuit",
        type=str,
        default=None,
        help="Update only specific circuit (e.g., 'albert_park')",
    )

    args = parser.parse_args()
    asyncio.run(main(args.circuit))
