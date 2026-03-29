"""Helpers for deterministic pregenerated image keys."""


def _display_suffix(display: str) -> str:
    """Return the filename/key suffix for a display variant."""
    if display == "spectra6":
        return "_spectra6"
    if display == "bwr":
        return "_bwr"
    if display == "bwry":
        return "_bwry"
    return ""


def get_calendar_image_key(
    lang: str,
    *,
    tz: str | None = None,
    default_timezone: str,
    display: str = "1bit",
    weather: str = "off",
) -> str:
    """Build a deterministic key for generated calendar variants."""
    key = f"calendar_{lang}"
    if tz and tz != default_timezone:
        key += f"_{tz.replace('/', '_')}"
    key += _display_suffix(display)
    if weather != "off":
        key += f"_weather_{weather}"
    return key


def get_teams_image_key(lang: str, year: int, *, display: str = "1bit") -> str:
    """Build a deterministic key for generated teams variants."""
    return f"teams_{year}_{lang}{_display_suffix(display)}"
