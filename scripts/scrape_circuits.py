#!/usr/bin/env python3
"""One-time scraper for F1 circuit data from formula1.com for 2026 season.

Uses only standard library + httpx (already installed).
Parses HTML with regex - not ideal but works for one-time scraping.
"""

import json
import re
import time
from pathlib import Path

import httpx

from app.utils.atomic_io import atomic_write_json

# List of 2026 races with their URL slugs and circuit IDs (matching Jolpica API)
RACES_2026 = [
    ("australia", "Australian Grand Prix", "albert_park"),
    ("china", "Chinese Grand Prix", "shanghai"),
    ("japan", "Japanese Grand Prix", "suzuka"),
    ("bahrain", "Bahrain Grand Prix", "bahrain"),
    ("saudi-arabia", "Saudi Arabian Grand Prix", "jeddah"),
    ("miami", "Miami Grand Prix", "miami"),
    ("canada", "Canadian Grand Prix", "villeneuve"),
    ("monaco", "Monaco Grand Prix", "monaco"),
    ("barcelona-catalunya", "Barcelona-Catalunya Grand Prix", "catalunya"),
    ("austria", "Austrian Grand Prix", "red_bull_ring"),
    ("great-britain", "British Grand Prix", "silverstone"),
    ("belgium", "Belgian Grand Prix", "spa"),
    ("hungary", "Hungarian Grand Prix", "hungaroring"),
    ("netherlands", "Dutch Grand Prix", "zandvoort"),
    ("italy", "Italian Grand Prix", "monza"),
    ("spain", "Spanish Grand Prix", "madring"),
    ("azerbaijan", "Azerbaijan Grand Prix", "baku"),
    ("singapore", "Singapore Grand Prix", "marina_bay"),
    ("united-states", "United States Grand Prix", "americas"),
    ("mexico", "Mexican Grand Prix", "rodriguez"),
    ("brazil", "Brazilian Grand Prix", "interlagos"),
    ("las-vegas", "Las Vegas Grand Prix", "las_vegas"),
    ("qatar", "Qatar Grand Prix", "losail"),
    ("united-arab-emirates", "Abu Dhabi Grand Prix", "yas_marina"),
]

BASE_URL = "https://www.formula1.com/en/racing/2026"


def extract_dt_dd_pairs(html: str) -> dict[str, str]:
    """Extract dt/dd pairs from HTML using regex."""
    # Pattern to match <dt>...</dt><dd>...</dd> pairs
    # This is a simplified pattern that works for the F1 page structure
    pattern = r"<dt[^>]*>([^<]+)</dt>\s*<dd[^>]*>([^<]*)</dd>"

    pairs = {}
    for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
        label = match.group(1).strip()
        value = match.group(2).strip()
        pairs[label] = value

    return pairs


def extract_fastest_lap_driver(html: str) -> tuple[str, str] | tuple[None, None]:
    """Extract fastest lap driver and year from span after dd."""
    # Look for pattern like: fastest lap time followed by driver name (year)
    pattern = r"Fastest lap.*?</dd>\s*<span[^>]*>([^<(]+)\s*\((\d{4})\)"
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip(), match.group(2)
    return None, None


def scrape_circuit_data(url_slug: str, client: httpx.Client) -> dict:
    """Scrape circuit data from a single race page."""
    url = f"{BASE_URL}/{url_slug}"
    print(f"  Fetching: {url}")

    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as e:
        print(f"    Error fetching {url}: {e}")
        return {}

    html = response.text

    # Extract dt/dd pairs
    pairs = extract_dt_dd_pairs(html)

    # Map to our data structure
    data: dict[str, str | int | None] = {
        "circuit_length": None,
        "first_grand_prix": None,
        "number_of_laps": None,
        "race_distance": None,
        "fastest_lap_time": None,
        "fastest_lap_driver": None,
        "fastest_lap_year": None,
    }

    for label, value in pairs.items():
        if "Circuit Length" in label:
            data["circuit_length"] = value if value else None
        elif "First Grand Prix" in label:
            data["first_grand_prix"] = value if value else None
        elif "Number of Laps" in label:
            data["number_of_laps"] = int(value) if value and value.isdigit() else None
        elif "Race Distance" in label:
            data["race_distance"] = value if value and value != "0km" else None
        elif "Fastest lap" in label.lower() or "lap time" in label.lower():
            data["fastest_lap_time"] = value if value and value != "--" else None

    # Try to extract driver info
    driver, year = extract_fastest_lap_driver(html)
    if driver:
        data["fastest_lap_driver"] = driver
        data["fastest_lap_year"] = year

    return data


def _load_existing_circuits(output_path: Path) -> dict[str, dict]:
    """Load the maintained circuit mapping or fail before an unsafe overwrite."""
    if not output_path.exists():
        return {}
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot safely update existing circuit data: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Cannot safely update circuit data: root must be an object")
    return payload


def _merge_circuit_entry(existing: dict, *, race_name: str, url_slug: str, scraped: dict) -> dict:
    """Merge scraped metadata without dropping maintained fields such as historical podiums."""
    present_scraped_values = {key: value for key, value in scraped.items() if value is not None}
    return {
        **existing,
        "race_name": race_name,
        "url_slug": url_slug,
        **present_scraped_values,
    }


def main() -> bool:
    """Main function to scrape all circuits."""
    output_path = Path(__file__).parent.parent / "app" / "assets" / "circuits_data.json"
    circuits_data = _load_existing_circuits(output_path)
    failures = 0

    # Use a session for connection reuse
    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        for url_slug, race_name, circuit_id in RACES_2026:
            print(f"Scraping: {race_name}")

            circuit_data = scrape_circuit_data(url_slug, client)
            if not circuit_data:
                failures += 1
                continue

            circuits_data[circuit_id] = _merge_circuit_entry(
                circuits_data.get(circuit_id, {}),
                race_name=race_name,
                url_slug=url_slug,
                scraped=circuit_data,
            )

            # Be polite - wait between requests
            time.sleep(1)

    # Save to JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, circuits_data)

    print(f"\nSaved circuit data to: {output_path}")
    print(f"Total circuits: {len(circuits_data)}")

    # Print summary
    print("\nSummary:")
    for circuit_id, data in circuits_data.items():
        length = data.get("circuit_length") or "N/A"
        laps = data.get("number_of_laps") or "N/A"
        print(f"  {circuit_id}: {length}, {laps} laps")
    return failures == 0


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
