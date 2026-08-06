"""Helpers for normalizing API result entries."""

from collections.abc import Mapping
from typing import Any

ResultEntry = Mapping[str, Any]


def get_result_mapping(entry: ResultEntry, key: str) -> ResultEntry:
    """Return a nested result mapping, or an empty mapping when malformed."""
    value = entry.get(key)
    if isinstance(value, Mapping):
        return value
    return {}


def parse_result_position(entry: object) -> int | None:
    """Return a numeric API result position, or None when the row is unusable."""
    if not isinstance(entry, Mapping):
        return None

    position = entry.get("position")
    if position is None:
        return None

    try:
        return int(position)
    except TypeError, ValueError:
        return None


def sort_entries_by_position(entries: object) -> list[tuple[int, ResultEntry]]:
    """Return valid result entries sorted by their numeric position."""
    if not isinstance(entries, list):
        return []

    positioned_entries = []
    for entry in entries:
        position = parse_result_position(entry)
        if position is not None and isinstance(entry, Mapping):
            positioned_entries.append((position, entry))

    return sorted(positioned_entries, key=lambda item: item[0])
