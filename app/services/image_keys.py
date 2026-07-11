"""Helpers for deterministic pregenerated image keys."""


def get_display_suffix(display: str) -> str:
    """Return the filename/key suffix for a display variant."""
    if display == "1bit":
        return ""
    if display == "spectra6":
        return "_spectra6"
    if display == "bwr":
        return "_bwr"
    if display == "bwry":
        return "_bwry"
    raise ValueError(f"Unsupported display mode: {display}")


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
    key += get_display_suffix(display)
    if weather != "off":
        key += f"_weather_{weather}"
    return key


def get_teams_image_key(lang: str, year: int, *, display: str = "1bit") -> str:
    """Build a deterministic key for generated teams variants."""
    return f"teams_{year}_{lang}{get_display_suffix(display)}"


def get_preview_filename(screen_type: str, lang: str) -> str:
    """Return the small homepage preview filename."""
    if screen_type not in {"calendar", "teams"}:
        raise ValueError(f"Unsupported screen type: {screen_type}")
    return f"preview_{screen_type}_{lang}.png"


def get_configure_preview_filename(
    screen_type: str,
    lang: str,
    *,
    display: str = "1bit",
    weather: str = "off",
) -> str:
    """Return the full-size configure preview filename shared by producer and route."""
    if screen_type not in {"calendar", "teams"}:
        raise ValueError(f"Unsupported screen type: {screen_type}")
    if screen_type == "teams" and weather != "off":
        raise ValueError("Teams previews do not support weather variants")

    filename = f"configure_{screen_type}_{lang}{get_display_suffix(display)}"
    if weather != "off":
        filename += f"_weather_{weather}"
    return f"{filename}.png"
