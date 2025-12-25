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
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = "https://api.jolpi.ca/ergast/f1"
CIRCUITS_PATH = Path(__file__).parent.parent / "app" / "assets" / "circuits_data.json"
CURRENT_YEAR = datetime.now().year


async def fetch_results(client: httpx.AsyncClient, circuit_id: str) -> dict | None:
    """Fetch latest qualifying and race results for a circuit."""
    for year in [CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2]:
        try:
            # Fetch qualifying
            q_url = f"{API_BASE}/{year}/circuits/{circuit_id}/qualifying.json?limit=3"
            q_resp = await client.get(q_url)
            q_resp.raise_for_status()
            q_data = q_resp.json()
            q_races = q_data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not q_races:
                continue

            # Fetch race results
            r_url = f"{API_BASE}/{year}/circuits/{circuit_id}/results.json?limit=3"
            r_resp = await client.get(r_url)
            r_resp.raise_for_status()
            r_data = r_resp.json()
            r_races = r_data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not r_races:
                continue

            # Parse qualifying
            qualifying = []
            for q in q_races[0].get("QualifyingResults", [])[:3]:
                qualifying.append(
                    {
                        "pos": int(q["position"]),
                        "code": q["Driver"]["code"],
                        "name": q["Driver"]["familyName"],
                        "team": q["Constructor"]["name"],
                        "time": q.get("Q3") or q.get("Q2") or q.get("Q1"),
                    }
                )

            # Parse race
            race = []
            for r in r_races[0].get("Results", [])[:3]:
                race.append(
                    {
                        "pos": int(r["position"]),
                        "code": r["Driver"]["code"],
                        "name": r["Driver"]["familyName"],
                        "team": r["Constructor"]["name"],
                        "time": r.get("Time", {}).get("time"),
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


async def main(circuit_filter: str | None = None) -> None:
    """Update historical data for circuits."""
    with open(CIRCUITS_PATH, encoding="utf-8") as f:
        circuits = json.load(f)

    # Determine which circuits to update
    if circuit_filter:
        circuit_ids = [circuit_filter] if circuit_filter in circuits else []
        if not circuit_ids:
            print(f"Circuit '{circuit_filter}' not found in circuits_data.json")
            return
    else:
        circuit_ids = list(circuits.keys())

    print(f"Updating {len(circuit_ids)} circuits...")

    updated_count = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for circuit_id in circuit_ids:
            print(f"  {circuit_id}...", end=" ", flush=True)

            results = await fetch_results(client, circuit_id)
            if results:
                circuits[circuit_id]["historical"] = results
                print(f"OK ({results['season']})")
                updated_count += 1
            else:
                print("No data")

            # Rate limiting
            await asyncio.sleep(2.5)

    # Save updated data
    with open(CIRCUITS_PATH, "w", encoding="utf-8") as f:
        json.dump(circuits, f, indent=2)

    print(f"\nUpdated {updated_count}/{len(circuit_ids)} circuits")
    print(f"Saved to {CIRCUITS_PATH}")


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
