#!/usr/bin/env python3
"""Scrape F1 teams and drivers data from Wikipedia.

Extracts: Entrant, Constructor, Chassis, Power Unit, Drivers (with numbers and rounds).
Outputs to app/assets/seasons/{year}_teams.json
"""

import json
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

WIKI_URL = "https://en.wikipedia.org/wiki/{year}_Formula_One_World_Championship"


def clean_text(text: str) -> str:
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_cell_lines(cell) -> list[str]:
    """Extract individual lines/items from a cell, handling <br> and nested structures."""
    lines = []

    for br in cell.find_all("br"):
        br.replace_with("\n")

    text = cell.get_text()
    for line in text.split("\n"):
        cleaned = clean_text(line)
        if cleaned:
            lines.append(cleaned)

    return lines


def get_flag_for_driver(cell, driver_name: str) -> str | None:
    """Find flag image associated with a driver name in the cell."""
    for link in cell.find_all("a"):
        link_text = clean_text(link.get_text())
        if link_text == driver_name:
            prev_sibling = link.find_previous_sibling("span")
            if prev_sibling:
                img = prev_sibling.find("img")
                if img:
                    alt = img.get("alt", "")
                    return alt.strip() if alt else None

            img = link.find_previous("img")
            if img:
                alt = img.get("alt", "")
                return alt.strip() if alt else None

    return None


def parse_drivers_from_columns(numbers_cell, names_cell, rounds_cell) -> list[dict]:
    """Parse driver info from the 3 separate columns."""
    drivers = []

    numbers = get_cell_lines(numbers_cell)
    names = get_cell_lines(names_cell)
    rounds_list = get_cell_lines(rounds_cell)

    while len(numbers) < len(names):
        numbers.append("")
    while len(rounds_list) < len(names):
        rounds_list.append("All")

    for i, name in enumerate(names):
        if not name or name in ["Driver name", "TBA"]:
            continue

        number_str = numbers[i] if i < len(numbers) else ""
        number = int(number_str) if number_str.isdigit() else None

        rounds_val = rounds_list[i] if i < len(rounds_list) else "All"
        if not rounds_val:
            rounds_val = "All"

        nationality = get_flag_for_driver(names_cell, name)

        drivers.append(
            {
                "number": number,
                "name": name,
                "nationality": nationality,
                "rounds": rounds_val,
            }
        )

    return drivers


def scrape_wiki_teams(year: int) -> dict:
    url = WIKI_URL.format(year=year)
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "F1-eInk-Calendar/1.0 (https://github.com/Rhiz3K/InkyCloud-F1)"
    }

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    target_table = None
    for table in soup.find_all("table", class_="wikitable"):
        caption = table.find("caption")
        if (
            caption
            and "team" in caption.get_text().lower()
            and "driver" in caption.get_text().lower()
        ):
            target_table = table
            print(f"Found table: {clean_text(caption.get_text())[:60]}")
            break

    if not target_table:
        return {"year": year, "teams": [], "error": "Table not found"}

    teams = []
    rows = target_table.find_all("tr")

    for row in rows:
        cells = row.find_all(["td", "th"], recursive=False)

        if len(cells) < 7:
            continue

        first_text = clean_text(cells[0].get_text())
        if first_text in ["Entrant", "No.", ""] or "Entrant" in first_text:
            continue

        entrant = clean_text(cells[0].get_text())
        constructor = clean_text(cells[1].get_text())
        chassis = clean_text(cells[2].get_text())
        power_unit = clean_text(cells[3].get_text())

        numbers_cell = cells[4]
        names_cell = cells[5]
        rounds_cell = cells[6]

        drivers = parse_drivers_from_columns(numbers_cell, names_cell, rounds_cell)

        team = {
            "entrant": entrant,
            "constructor": constructor,
            "chassis": chassis,
            "power_unit": power_unit,
            "drivers": drivers,
        }
        teams.append(team)

    return {"year": year, "teams": teams}


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    print(f"Scraping F1 {year} teams data from Wikipedia\n")

    data = scrape_wiki_teams(year)

    teams = data.get("teams", [])
    driver_count = sum(len(t.get("drivers", [])) for t in teams)
    print(f"\nFound {len(teams)} teams with {driver_count} drivers")

    output_path = (
        Path(__file__).parent.parent
        / "app"
        / "assets"
        / "seasons"
        / f"{year}_teams.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
