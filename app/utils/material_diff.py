"""Helpers for detecting meaningful changes in generated static data."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def has_material_change(
    refreshed_payload: dict[str, Any],
    existing_payload: dict[str, Any] | None,
    *,
    ignored_keys: Iterable[str],
) -> bool:
    """Return whether payloads differ after known refresh metadata is removed."""
    if existing_payload is None:
        return True

    ignored = set(ignored_keys)
    refreshed_material = {
        key: value for key, value in refreshed_payload.items() if key not in ignored
    }
    existing_material = {
        key: value for key, value in existing_payload.items() if key not in ignored
    }
    return refreshed_material != existing_material
