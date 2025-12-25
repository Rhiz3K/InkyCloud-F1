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

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = "https://api.jolpi.ca/ergast/f1"
SEASONS_DIR = Path(__file__).parent.parent / "app" / "assets" / "seasons"


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


async def main(years: list[int]) -> None:
    """Download and save season data for specified years."""
    SEASONS_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=30) as client:
        for year in years:
            try:
                data = await fetch_season(client, year)
                output_path = SEASONS_DIR / f"{year}.json"

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
        years = [int(y.strip()) for y in args.years.split(",")]
    else:
        current_year = datetime.now().year
        years = [current_year, current_year + 1]

    print(f"Updating seasons: {years}")
    asyncio.run(main(years))
