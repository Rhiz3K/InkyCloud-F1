"""Season-facing metadata helpers for standings payloads."""

DRIVER_NUMBERS = {
    "VER": 1,
    "NOR": 4,
    "LEC": 16,
    "SAI": 55,
    "HAM": 44,
    "RUS": 63,
    "PIA": 81,
    "ALO": 14,
    "STR": 18,
    "GAS": 10,
    "OCO": 31,
    "ALB": 23,
    "TSU": 22,
    "RIC": 3,
    "HUL": 27,
    "MAG": 20,
    "BOT": 77,
    "ZHO": 24,
    "SAR": 2,
    "LAW": 30,
    "BEA": 87,
    "COL": 43,
    "DOO": 7,
    "ANT": 12,
    "HAD": 6,
    "BOR": 5,
}

DRIVER_NUMBERS_BY_YEAR = {
    2026: {
        "NOR": 1,
        "PIA": 81,
        "RUS": 63,
        "ANT": 12,
        "LEC": 16,
        "HAM": 44,
        "VER": 3,
        "HAD": 6,
        "LAW": 30,
        "LIN": 41,
        "ALO": 14,
        "STR": 18,
        "GAS": 10,
        "COL": 43,
        "ALB": 23,
        "SAI": 55,
        "HUL": 27,
        "BOR": 5,
        "OCO": 31,
        "BEA": 87,
        "PER": 11,
        "BOT": 77,
    }
}


def get_driver_number(driver_code: str, year: int) -> int | None:
    """Resolve a season-aware car number, falling back to legacy metadata."""
    return DRIVER_NUMBERS_BY_YEAR.get(year, {}).get(driver_code, DRIVER_NUMBERS.get(driver_code))

TEAM_ID_MAP = {
    "McLaren": "mclaren",
    "Ferrari": "ferrari",
    "Red Bull": "red_bull",
    "Mercedes": "mercedes",
    "Aston Martin": "aston_martin",
    "Alpine": "alpine",
    "Williams": "williams",
    "RB": "racing_bulls",
    "Racing Bulls": "racing_bulls",
    "Haas F1 Team": "haas",
    "Haas": "haas",
    "Kick Sauber": "sauber",
    "Sauber": "sauber",
    "Alfa Romeo": "sauber",
}
