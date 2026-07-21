"""Text measurement, formatting, and roster selection for renderers."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from PIL import ImageDraw

from app.services.font_utils import CJK_LANG_CODES, fit_ui_font

_ROUND_RANGE_RE = re.compile(r"^(\d+)(?:\s*[-–—]\s*(\d+))?$")


def _driver_round_window(rounds: str) -> tuple[int, int] | None:
    """Parse a driver round label into an inclusive normalized range."""
    normalized = rounds.strip()
    if not normalized or normalized.lower() == "all":
        return None
    match = _ROUND_RANGE_RE.fullmatch(normalized)
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return (min(start, end), max(start, end))


def select_active_team_drivers(drivers: Sequence, limit: int = 2) -> list:
    """Select the drivers active at the latest round represented by season roster data."""
    if limit <= 0:
        return []

    windows = [
        (driver, _driver_round_window(getattr(driver, "rounds", "All"))) for driver in drivers
    ]
    latest_round = max((window[1] for _, window in windows if window is not None), default=None)
    if latest_round is None:
        active = [driver for driver, _ in windows]
    else:
        active = [
            driver
            for driver, window in windows
            if window is None or window[0] <= latest_round <= window[1]
        ]

    if len(active) < limit:
        active_ids = {id(driver) for driver in active}
        active.extend(driver for driver, _ in windows if id(driver) not in active_ids)
    return sorted(active, key=lambda driver: driver.position or 99)[:limit]


def split_teams_for_columns(teams: list) -> tuple[list, list]:
    """Split teams into balanced left and right columns."""
    if not teams:
        return [], []

    left_count = math.ceil(len(teams) / 2)
    return teams[:left_count], teams[left_count:]


def get_text_y(
    draw: ImageDraw.ImageDraw,
    font,
    row_h: int,
    row_y: int,
    text: str = "Ay",
) -> int:
    """Align text vertically within a row using the provided text metrics."""
    bbox = draw.textbbox((0, 0), text, font=font)
    height = bbox[3] - bbox[1]
    top_offset = bbox[1]
    return int(row_y + (row_h - height) // 2 - top_offset)


def right_align_x(draw: ImageDraw.ImageDraw, text: str, right_edge: int, font) -> int:
    """Return the x-coordinate that right-aligns text to the given edge."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(right_edge - bbox[2])


def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """Measure rendered text width for the active draw context."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def clamp_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Clamp text to fit into a maximum width using an ellipsis."""
    if max_width <= 0:
        return ""
    if text_width(draw, text, font) <= max_width:
        return text

    ellipsis = "..."
    trimmed = text
    while trimmed and text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ""


def normalize_team_power_unit(constructor: str, power_unit: str | None) -> str:
    """Shorten Red Bull power-unit labels in teams headers."""
    if not power_unit:
        return ""

    normalized = power_unit.replace("-AMG", "").strip()
    constructor_name = (constructor or "").lower()
    is_red_bull_team = (
        "red bull" in constructor_name
        or "racing bulls" in constructor_name
        or constructor_name == "rb"
        or constructor_name.startswith("rb ")
    )
    if not is_red_bull_team:
        return normalized

    if normalized.startswith("Red Bull "):
        remainder = normalized.removeprefix("Red Bull ").strip()
        return f"RB {remainder}" if remainder else "RB"

    return normalized.replace("Red Bull", "RB")


def format_team_driver_display_name(name: str) -> str:
    """Format a driver name as `Given SURNAME` for team cards."""
    name_parts = name.replace(" Jr.", "").replace(" jr.", "").split()
    if len(name_parts) >= 2:
        given = name_parts[0]
        surname = " ".join(name_parts[1:]).upper()
        return f"{given} {surname}"
    return name.upper()


def format_points(value: float | int | None) -> str:
    """Format points while preserving half-points for display."""
    if value is None or value == 0:
        return "0"
    value_float = float(value)
    if value_float.is_integer():
        return str(int(value_float))
    return f"{value_float:.1f}"


def build_team_header_values(team) -> tuple[str, str, str, str]:
    """Build normalized constructor header strings for a team card."""
    constructor = team.constructor_name or team.entrant or ""
    team_name = constructor.split("-")[0].replace(" Aramco", "").replace("Kick ", "").strip()
    chassis = team.chassis or ""
    power_unit = normalize_team_power_unit(constructor, team.power_unit)
    meta_text = " | ".join(part for part in (chassis, power_unit) if part)
    team_pos = str(team.position) if team.position else "—"
    return team_name, meta_text, team_pos, format_points(team.points)


def normalize_session_name(name: str) -> str:
    """Normalize API/static session variants to a stable translation key suffix."""
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
    aliases = {
        "practice1": "fp1",
        "practice2": "fp2",
        "practice3": "fp3",
        "firstpractice": "fp1",
        "secondpractice": "fp2",
        "thirdpractice": "fp3",
        "sprintqualifying": "sprintqualifying",
        "sprintshootout": "sprintqualifying",
        "shootout": "sprintqualifying",
    }
    return aliases.get(normalized, normalized)


def abbreviate_schedule_term(term: str, lang_code: str) -> str:
    """Reduce a localized schedule term to its leading letter or character."""
    stripped = term.strip()
    if not stripped:
        return term
    first_char = stripped[0]
    if lang_code in CJK_LANG_CODES:
        return first_char
    return f"{first_char}."


def build_sprint_qualifying_label(
    translator: Mapping[str, str],
    lang_code: str,
    *,
    abbreviated: bool,
) -> str:
    """Compose the sprint qualifying label from localized sprint/qualifying text."""
    sprint_label = translator.get("session_sprint", "Sprint")
    qualifying_label = translator.get("session_qualifying", "Qualifying")
    separator = "" if lang_code in CJK_LANG_CODES else " "

    if abbreviated:
        qualifying_label = abbreviate_schedule_term(qualifying_label, lang_code)

    return f"{sprint_label}{separator}{qualifying_label}"


def get_dedicated_sprint_qualifying_label(translator: Mapping[str, str]) -> str | None:
    """Return a locale-specific sprint-qualifying label when one is defined."""
    label = translator.get("session_sprintqualifying")
    if isinstance(label, str) and label:
        return label
    return None


def translate_session_name(name: str, translator: Mapping[str, str], lang_code: str) -> str:
    """Translate session names while normalizing API/static variants."""
    if not name:
        return ""

    normalized = normalize_session_name(name)
    if normalized == "sprintqualifying":
        return get_dedicated_sprint_qualifying_label(translator) or build_sprint_qualifying_label(
            translator, lang_code, abbreviated=False
        )

    direct_key = f"session_{name.lower()}"
    if direct_key in translator:
        return translator[direct_key]

    normalized_key = f"session_{normalized}"
    return translator.get(normalized_key, name)


def format_schedule_session_name(
    draw: ImageDraw.ImageDraw,
    name: str,
    max_width: int,
    lang_code: str,
    translator: Mapping[str, str],
) -> str:
    """Return the best-fitting localized schedule label for a session."""
    if normalize_session_name(name) != "sprintqualifying":
        return translate_session_name(name, translator, lang_code)

    dedicated_label = get_dedicated_sprint_qualifying_label(translator)
    if dedicated_label:
        return dedicated_label

    full_label = build_sprint_qualifying_label(translator, lang_code, abbreviated=False)
    full_font = fit_ui_font(
        draw,
        lang_code,
        full_label,
        max_width=max_width,
        base_size=20,
        min_size=15,
        bold=True,
    )
    full_bbox = draw.textbbox((0, 0), full_label, font=full_font)
    if full_bbox[2] - full_bbox[0] <= max_width:
        return full_label

    return build_sprint_qualifying_label(translator, lang_code, abbreviated=True)


def get_team_logo_key(constructor: str) -> str | None:
    """Map a constructor name to the corresponding team logo asset key."""
    name = constructor.lower()
    if "audi" in name:
        return "audi"
    if "cadillac" in name:
        return "cadillac"
    if "mclaren" in name:
        return "mclaren"
    if "williams" in name:
        return "williams"
    if "aston martin" in name:
        return "aston_martin"
    if (
        name == "rb"
        or name.startswith("rb ")
        or " rb " in name
        or "racing bulls" in name
        or "visa" in name
    ):
        return "racing_bulls"
    if "red bull" in name:
        return "red_bull"
    if "haas" in name:
        return "haas"
    if "sauber" in name or "stake" in name or "kick" in name:
        return "sauber"
    if "alpine" in name:
        return "alpine"
    if "mercedes" in name:
        return "mercedes"
    if "ferrari" in name:
        return "ferrari"
    return None


def fit_result_text(
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
    pos: int,
    driver: str,
    team: str,
) -> str:
    """Fit historical results text into the available width."""

    def get_width(text: str) -> int:
        """Measure candidate result text with the active drawing font."""
        return int(draw.textbbox((0, 0), text, font=font)[2])

    full = f"{pos}. {driver} ({team})"
    if get_width(full) <= max_width:
        return full

    for i in range(len(team), 2, -1):
        short_team = team[:i] + ".."
        text = f"{pos}. {driver} ({short_team})"
        if get_width(text) <= max_width:
            return text

    short_team = team[:3] + ".."

    for i in range(len(driver), 2, -1):
        short_driver = driver[:i] + "."
        text = f"{pos}. {short_driver} ({short_team})"
        if get_width(text) <= max_width:
            return text

    return f"{pos}. {driver[:5]}.. ({team[:3]}..)"
