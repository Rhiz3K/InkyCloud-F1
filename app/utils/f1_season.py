"""F1 season helper utilities."""

from __future__ import annotations

from datetime import datetime, timezone


def get_current_f1_season() -> int:
    """Get the current F1 season based on first race dates."""
    now = datetime.now(timezone.utc)
    season_2026_start = datetime(2026, 3, 8, tzinfo=timezone.utc)
    season_2025_start = datetime(2025, 3, 16, tzinfo=timezone.utc)

    if now >= season_2026_start:
        return 2026
    if now >= season_2025_start:
        return 2025
    return 2024
