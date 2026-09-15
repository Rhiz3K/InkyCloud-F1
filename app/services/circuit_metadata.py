"""Shared circuit and country metadata used across services and renderers."""

from __future__ import annotations

CIRCUIT_ID_MAP: dict[str, str] = {
    "vegas": "las_vegas",  # API uses 'vegas', we use 'las_vegas'
}


def canonical_circuit_id(circuit_id: str) -> str:
    """Resolve provider aliases to the circuit IDs used by bundled data and artwork."""
    return CIRCUIT_ID_MAP.get(circuit_id, circuit_id)


COUNTRY_MAP: dict[str, str] = {
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
