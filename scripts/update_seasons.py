#!/usr/bin/env python3
"""
Download F1 season calendars from Jolpica API and save to static JSON files.

Usage:
    python scripts/update_seasons.py [--years 2025,2026]

This script is meant to be run:
- Manually when FIA announces calendar changes
- Via GitHub Action in January each year
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = "https://api.jolpi.ca/ergast/f1"
SEASONS_DIR = Path(__file__).parent.parent / "app" / "assets" / "seasons"


def _has_scheduled_round(race: dict[str, Any]) -> bool:
    """Return True when the race still has an active calendar round."""
    round_value = race.get("round")
    if round_value in (None, ""):
        return False

    try:
        return int(round_value) > 0
    except (TypeError, ValueError):
        return False


def _race_identity(race: dict[str, Any]) -> tuple[str, str, str, str]:
    """Build a stable identity for deduplicating races across refreshes."""
    circuit_id = race.get("Circuit", {}).get("circuitId", "")
    return (
        str(race.get("season", "")),
        str(circuit_id).lower(),
        str(race.get("date", "")),
        str(race.get("raceName", "")).lower(),
    )


def _load_existing_season(output_path: Path) -> dict[str, Any] | None:
    """Load the current static season file when present."""
    if not output_path.exists():
        return None

    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: failed to read existing season file {output_path}: {exc}")
        return None


def preserve_cancelled_races(
    season_payload: dict[str, Any], existing_payload: dict[str, Any] | None
) -> dict[str, Any]:
    """Keep previously tracked cancelled races when the live API omits them."""
    merged_races = list(season_payload.get("races", []))
    seen_races = {_race_identity(race) for race in merged_races}
    preserved_cancelled: list[dict[str, Any]] = []

    for race in (existing_payload or {}).get("races", []):
        if _has_scheduled_round(race):
            continue

        race_key = _race_identity(race)
        if race_key in seen_races:
            continue

        preserved_cancelled.append(race)
        seen_races.add(race_key)

    preserved_cancelled.sort(key=lambda race: (str(race.get("date", "")), _race_identity(race)))
    merged_races.extend(preserved_cancelled)

    return {
        **season_payload,
        "total_races": len(merged_races),
        "races": merged_races,
    }


async def fetch_season(client: httpx.AsyncClient, year: int) -> dict:
    """Fetch full season calendar from API."""
    url = f"{API_BASE}/{year}.json?limit=30"
    print(f"Fetching {url}...")

    response = await client.get(url)
    response.raise_for_status()

    data = response.json()
    races = data["MRData"]["RaceTable"]["Races"]

    return {
        "season": str(year),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_races": len(races),
        "races": races,
    }


async def main(target_years: list[int]) -> None:
    """Download and save season data for specified years."""
    SEASONS_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=30) as client:
        for year in target_years:
            try:
                output_path = SEASONS_DIR / f"{year}.json"
                existing_data = _load_existing_season(output_path)
                data = preserve_cancelled_races(await fetch_season(client, year), existing_data)

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

                print(f"  Saved {data['total_races']} races to {output_path}")

                # Rate limiting
                await asyncio.sleep(2)

            except httpx.HTTPStatusError as e:
                print(f"  Error fetching {year}: HTTP {e.response.status_code}")
            except Exception as e:
                print(f"  Error fetching {year}: {e}")

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update F1 season calendar data")
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help="Comma-separated list of years (default: current and next year)",
    )

    args = parser.parse_args()

    if args.years:
        years_to_update = [int(y.strip()) for y in args.years.split(",")]
    else:
        current_year = datetime.now().year
        years_to_update = [current_year, current_year + 1]

    print(f"Updating seasons: {years_to_update}")
    asyncio.run(main(years_to_update))
