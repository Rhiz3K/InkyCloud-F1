import asyncio
import json

import httpx

URL = "https://api.jolpi.ca/ergast/f1/2026.json"


async def main():
    print(f"Fetching data from {URL}...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(URL)
            response.raise_for_status()
            data = response.json()

            # Find China race
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            china_race = next(
                (
                    r
                    for r in races
                    if r.get("Circuit", {}).get("Location", {}).get("country") == "China"
                ),
                None,
            )

            if china_race:
                print("Found China GP 2026:")
                output = json.dumps(china_race, indent=2)
                print(output)
                with open("api_response_china.json", "w") as f:
                    f.write(output)
            else:
                print("China GP not found in 2026 calendar.")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
