import asyncio
import json
from dataclasses import asdict, dataclass

import httpx

BASE_URL = "https://api.jolpi.ca/ergast/f1"
LIMIT = 100  # Max limit per docs


@dataclass
class MaxValues:
    race_name: str = ""
    circuit_name: str = ""
    circuit_locality: str = ""
    circuit_country: str = ""
    driver_given_name: str = ""
    driver_family_name: str = ""
    driver_code: str = ""
    constructor_name: str = ""


async def fetch_all_pages(client, endpoint, key_path, processor_func, max_vals):
    offset = 0
    total = None

    while total is None or offset < total:
        print(f"Fetching {endpoint} offset={offset}...")
        try:
            resp = await client.get(
                f"{BASE_URL}{endpoint}.json?limit={LIMIT}&offset={offset}"
            )
            resp.raise_for_status()
            data = resp.json()

            mr_data = data["MRData"]
            if total is None:
                total = int(mr_data["total"])
                print(f"Total items for {endpoint}: {total}")

            # Navigate to the list of items
            items_root = mr_data
            for key in key_path:
                items_root = items_root.get(key, {})

            # items_root should be the list now
            if isinstance(items_root, list):
                for item in items_root:
                    processor_func(item, max_vals)

            offset += LIMIT
            await asyncio.sleep(1.1)  # Be strictly above 1s to be safe

        except Exception as e:
            print(f"Error fetching {endpoint} at offset {offset}: {e}")
            break


def process_driver(driver, max_vals):
    if len(driver["givenName"]) > len(max_vals.driver_given_name):
        max_vals.driver_given_name = driver["givenName"]
    if len(driver["familyName"]) > len(max_vals.driver_family_name):
        max_vals.driver_family_name = driver["familyName"]
    if "code" in driver and len(driver["code"]) > len(max_vals.driver_code):
        max_vals.driver_code = driver["code"]


def process_constructor(constructor, max_vals):
    if len(constructor["name"]) > len(max_vals.constructor_name):
        max_vals.constructor_name = constructor["name"]


def process_circuit(circuit, max_vals):
    if len(circuit["circuitName"]) > len(max_vals.circuit_name):
        max_vals.circuit_name = circuit["circuitName"]

    loc = circuit["Location"]
    if len(loc["locality"]) > len(max_vals.circuit_locality):
        max_vals.circuit_locality = loc["locality"]
    if len(loc["country"]) > len(max_vals.circuit_country):
        max_vals.circuit_country = loc["country"]


def process_race(race, max_vals):
    # Only care about >= 2000 for races as requested, or just check all (super set is fine)
    # User said "since 2000"
    season = int(race.get("season", 0))
    if season >= 2000:
        if len(race["raceName"]) > len(max_vals.race_name):
            max_vals.race_name = race["raceName"]


async def main():
    max_vals = MaxValues()
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Drivers
        await fetch_all_pages(
            client, "/drivers", ["DriverTable", "Drivers"], process_driver, max_vals
        )

        # Constructors
        await fetch_all_pages(
            client,
            "/constructors",
            ["ConstructorTable", "Constructors"],
            process_constructor,
            max_vals,
        )

        # Circuits
        await fetch_all_pages(
            client, "/circuits", ["CircuitTable", "Circuits"], process_circuit, max_vals
        )

        # Races
        # /races endpoint gives all races ever
        await fetch_all_pages(
            client, "/races", ["RaceTable", "Races"], process_race, max_vals
        )

    print(json.dumps(asdict(max_vals), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
