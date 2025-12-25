#!/usr/bin/env python3
"""Download country flags for F1 circuits.

This script downloads:
- Waving flag images (PNG) from flagpedia.net for UI
- Flat flag images (PNG) from flagcdn.com for BMP renderer

Usage:
    python scripts/download_flags.py
"""

import sys
from pathlib import Path

import httpx

# F1 Country to ISO 2-letter code mapping
COUNTRY_MAP = {
    "Australia": "au",
    "Austria": "at",
    "Azerbaijan": "az",
    "Bahrain": "bh",
    "Belgium": "be",
    "Brazil": "br",
    "Canada": "ca",
    "China": "cn",
    "France": "fr",
    "Germany": "de",
    "Hungary": "hu",
    "Italy": "it",
    "Japan": "jp",
    "Mexico": "mx",
    "Monaco": "mc",
    "Netherlands": "nl",
    "Portugal": "pt",
    "Qatar": "qa",
    "Russia": "ru",
    "Saudi Arabia": "sa",
    "Singapore": "sg",
    "Spain": "es",
    "Turkey": "tr",
    "UAE": "ae",
    "United Arab Emirates": "ae",
    "UK": "gb",
    "United Kingdom": "gb",
    "USA": "us",
    "United States": "us",
}

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
FLAGS_WAVING_DIR = PROJECT_ROOT / "app" / "assets" / "flags"
FLAGS_FLAT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_flat"

# Waving flag size (from flagpedia.net icon format)
WAVING_WIDTH = 108
WAVING_HEIGHT = 81

# Flat flag size (from flagcdn.com)
FLAT_WIDTH = 80


def download_waving_flag(iso_code: str, output_path: Path) -> bool:
    """Download a waving flag PNG from flagpedia.net."""
    url = f"https://flagpedia.net/data/flags/icon/{WAVING_WIDTH}x{WAVING_HEIGHT}/{iso_code}.png"
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        if response.status_code == 200:
            output_path.write_bytes(response.content)
            return True
        print(f"  Failed: {iso_code} HTTP {response.status_code}")
        return False
    except Exception as e:
        print(f"  Failed: {iso_code} {e}")
        return False


def download_flat_flag(iso_code: str, output_path: Path) -> bool:
    """Download a flat flag PNG from flagcdn.com."""
    url = f"https://flagcdn.com/w{FLAT_WIDTH}/{iso_code}.png"
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        if response.status_code == 200:
            output_path.write_bytes(response.content)
            return True
        print(f"  Failed: {iso_code} HTTP {response.status_code}")
        return False
    except Exception as e:
        print(f"  Failed: {iso_code} {e}")
        return False


def download_flags(name: str, output_dir: Path, download_func) -> tuple[int, int]:
    """Download all flags using specified function."""
    print(f"\n{name}")
    print("-" * 50)

    output_dir.mkdir(parents=True, exist_ok=True)
    unique_codes = set(COUNTRY_MAP.values())

    success = 0
    total_size = 0

    for iso_code in sorted(unique_codes):
        output_path = output_dir / f"{iso_code}.png"
        if download_func(iso_code, output_path):
            size = output_path.stat().st_size
            total_size += size
            print(f"  {iso_code}.png ({size / 1024:.1f} KB)")
            success += 1

    print(f"  Total: {success}/{len(unique_codes)} flags, {total_size / 1024:.1f} KB")
    return success, len(unique_codes)


def main():
    """Download all F1 country flags."""
    print("=" * 60)
    print(" F1 Country Flag Downloader")
    print("=" * 60)

    # Download waving flags for UI
    waving_ok, waving_total = download_flags(
        "Waving flags (UI) - flagpedia.net", FLAGS_WAVING_DIR, download_waving_flag
    )

    # Download flat flags for BMP renderer
    flat_ok, flat_total = download_flags(
        "Flat flags (Renderer) - flagcdn.com", FLAGS_FLAT_DIR, download_flat_flag
    )

    print("\n" + "=" * 60)
    print(f" Waving: {FLAGS_WAVING_DIR}")
    print(f" Flat:   {FLAGS_FLAT_DIR}")
    print("=" * 60)

    if waving_ok < waving_total or flat_ok < flat_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
