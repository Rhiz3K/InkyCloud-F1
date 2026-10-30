"""Immutable palette and asset policies shared by renderer variants."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from PIL import Image

from app.services.renderer_text import TEAM_COLORS, get_team_key

RGB: TypeAlias = tuple[int, int, int]
Fill: TypeAlias = int | RGB
TrackPreparer: TypeAlias = Callable[[Image.Image, int, int, Any], Image.Image]

BLACK_RGB: RGB = (0x00, 0x00, 0x00)
WHITE_RGB: RGB = (0xFF, 0xFF, 0xFF)


def redmean_distance_sq(first: RGB, second: RGB) -> float:
    """Return the squared "redmean" weighted RGB distance between two colors.

    The redmean formula (https://www.compuphase.com/cmetric.htm) weights the red and
    blue channel differences by the mean red level and the green difference by 4. It
    tracks perceived difference far better than plain Euclidean RGB while staying a
    dependency-free approximation; full CIELAB/ΔE would be overkill for snapping a
    dozen team colors onto a 3-6 entry E-Ink palette.
    """
    red_mean = (first[0] + second[0]) / 2
    delta_red = first[0] - second[0]
    delta_green = first[1] - second[1]
    delta_blue = first[2] - second[2]
    return (
        (2 + red_mean / 256) * delta_red**2
        + 4 * delta_green**2
        + (2 + (255 - red_mean) / 256) * delta_blue**2
    )


def nearest_palette_color(color: RGB, palette: Sequence[RGB]) -> RGB:
    """Return the palette entry nearest to ``color`` by redmean distance.

    Exact ties resolve to the earliest palette entry.
    """
    if not palette:
        raise ValueError("palette must not be empty")
    return min(palette, key=lambda entry: redmean_distance_sq(color, entry))


def _linearize_channel(value: int) -> float:
    """Convert one 8-bit sRGB channel to linear light per WCAG 2.x."""
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(color: RGB) -> float:
    """Return the WCAG 2.x relative luminance (0 black .. 1 white) of an sRGB color."""
    red, green, blue = (_linearize_channel(channel) for channel in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: RGB, second: RGB) -> float:
    """Return the WCAG 2.x contrast ratio (1..21) between two sRGB colors."""
    first_luminance = relative_luminance(first)
    second_luminance = relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def team_header_colors(
    team_color: RGB | None,
    palette: Sequence[RGB],
    *,
    background: RGB,
    default_fill: Fill,
    default_text: Fill,
    dark_text: RGB = BLACK_RGB,
    light_text: RGB = WHITE_RGB,
) -> tuple[Fill, Fill]:
    """Return the ``(fill, text_fill)`` pair for a team card header.

    ``team_color`` is snapped to the nearest ``palette`` entry. The text uses whichever
    of ``dark_text`` / ``light_text`` has the higher contrast ratio against that fill,
    preferring ``light_text`` on ties. Without a team color, or when the nearest entry
    is the card ``background`` (the header would disappear), the defaults are returned.
    """
    if team_color is None:
        return default_fill, default_text
    fill = nearest_palette_color(team_color, palette)
    if fill == background:
        return default_fill, default_text
    if contrast_ratio(fill, dark_text) > contrast_ratio(fill, light_text):
        return fill, dark_text
    return fill, light_text


def make_team_header_colors_for(
    colors: Any, *, default_fill: Fill, default_text: Fill
) -> Callable[[str], tuple[Fill, Fill]]:
    """Build a constructor-name lookup of palette-mapped team header colors."""
    palette: list[RGB] = list(colors.PALETTE)

    def team_header_colors_for(constructor_name: str) -> tuple[Fill, Fill]:
        """Return the themed ``(fill, text_fill)`` header pair for a constructor."""
        team_key = get_team_key(constructor_name)
        return team_header_colors(
            TEAM_COLORS.get(team_key) if team_key else None,
            palette,
            background=colors.WHITE,
            default_fill=default_fill,
            default_text=default_text,
            dark_text=colors.BLACK,
            light_text=colors.WHITE,
        )

    return team_header_colors_for


@dataclass(frozen=True, slots=True)
class RenderTheme:
    """All renderer behavior that changes with the target display palette."""

    colors: Any
    track_directory: Callable[[], Path]
    flags_directories: Callable[[], Path | tuple[Path, ...]]
    images_directory: Callable[[], Path]
    prepare_track_image: TrackPreparer
    normalize_track_image: Callable[[Image.Image], Image.Image]
    prepare_flag_image: Callable[[Image.Image], Image.Image]
    header_left_fill: Fill
    header_divider_fill: Fill
    header_right_fill: Fill
    header_text_fill: Fill
    text_fill: Fill
    accent_fill: Fill
    countdown_fill: Fill
    countdown_outline_fill: Fill
    countdown_text_fill: Fill
    error_title_fill: Fill
    error_text_fill: Fill
    team_header_fill: Fill
    team_header_text_fill: Fill
    team_header_colors_for: Callable[[str], tuple[Fill, Fill]]
    team_outline_fill: Fill
    team_panel_fill: Fill
    team_panel_outline_fill: Fill
    team_position_fill: Callable[[int | None], Fill]
    team_points_fill: Fill
    driver_badge_colors: Callable[[int | None], tuple[Fill, Fill]]
    driver_number_fill: Fill
    driver_resample: Image.Resampling
    session_fills: tuple[tuple[str, Fill], ...]
    session_shadow_fill: Fill | None
    track_placeholder_fill: Fill
    results_split_position_prefix: bool
    include_bold_circuit_location: bool
    use_bold_team_tech_font: bool
    qualifying_translation_key: str
    race_translation_key: str


def make_monochrome_theme(
    *,
    colors: Any,
    track_directory: Callable[[], Path],
    flags_directories: Callable[[], Path | tuple[Path, ...]],
    images_directory: Callable[[], Path],
    prepare_track_image: TrackPreparer,
) -> RenderTheme:
    """Build the fixed one-bit rendering policy."""
    return RenderTheme(
        colors=colors,
        track_directory=track_directory,
        flags_directories=flags_directories,
        images_directory=images_directory,
        prepare_track_image=prepare_track_image,
        normalize_track_image=lambda image: image,
        prepare_flag_image=lambda image: image.copy(),
        header_left_fill=colors.WHITE,
        header_divider_fill=colors.BLACK,
        header_right_fill=colors.BLACK,
        header_text_fill=colors.WHITE,
        text_fill=colors.BLACK,
        accent_fill=colors.BLACK,
        countdown_fill=colors.BLACK,
        countdown_outline_fill=colors.BLACK,
        countdown_text_fill=colors.WHITE,
        error_title_fill=colors.BLACK,
        error_text_fill=colors.BLACK,
        team_header_fill=colors.BLACK,
        team_header_text_fill=colors.WHITE,
        team_header_colors_for=lambda _constructor_name: (colors.BLACK, colors.WHITE),
        team_outline_fill=colors.BLACK,
        team_panel_fill=colors.WHITE,
        team_panel_outline_fill=colors.BLACK,
        team_position_fill=lambda _position: colors.BLACK,
        team_points_fill=colors.BLACK,
        driver_badge_colors=lambda position: (
            (colors.BLACK, colors.WHITE) if position in {2, 3} else (colors.WHITE, colors.BLACK)
        ),
        driver_number_fill=colors.BLACK,
        driver_resample=Image.Resampling.NEAREST,
        session_fills=(
            ("race", colors.BLACK),
            ("qualifying", colors.BLACK),
            ("sprint", colors.BLACK),
            ("practice", colors.BLACK),
            ("default", colors.BLACK),
        ),
        session_shadow_fill=None,
        track_placeholder_fill=colors.BLACK,
        results_split_position_prefix=False,
        include_bold_circuit_location=False,
        use_bold_team_tech_font=False,
        qualifying_translation_key="qualifying",
        race_translation_key="race",
    )


def make_color_theme(
    *,
    colors: Any,
    track_directory: Callable[[], Path],
    flags_directories: Callable[[], Path | tuple[Path, ...]],
    images_directory: Callable[[], Path],
    prepare_track_image: TrackPreparer,
) -> RenderTheme:
    """Build the common RGB-composition policy for color E-Ink palettes."""
    black = colors.BLACK
    white = colors.WHITE
    red = colors.RED
    yellow = getattr(colors, "YELLOW", black)
    green = getattr(colors, "GREEN", black)
    blue = getattr(colors, "BLUE", black)
    return RenderTheme(
        colors=colors,
        track_directory=track_directory,
        flags_directories=flags_directories,
        images_directory=images_directory,
        prepare_track_image=prepare_track_image,
        normalize_track_image=lambda image: image.convert("RGB"),
        prepare_flag_image=lambda image: image.convert("RGB"),
        header_left_fill=white,
        header_divider_fill=red,
        header_right_fill=red,
        header_text_fill=white,
        text_fill=black,
        accent_fill=red,
        countdown_fill=red,
        countdown_outline_fill=red,
        countdown_text_fill=white,
        error_title_fill=red,
        error_text_fill=black,
        team_header_fill=black,
        team_header_text_fill=white,
        team_header_colors_for=make_team_header_colors_for(
            colors, default_fill=black, default_text=white
        ),
        team_outline_fill=black,
        team_panel_fill=white,
        team_panel_outline_fill=black,
        team_position_fill=lambda position: red if position == 1 else black,
        team_points_fill=black,
        driver_badge_colors=lambda position: (
            (red, white)
            if position == 1
            else (black, white)
            if position in {2, 3}
            else (white, black)
        ),
        driver_number_fill=black,
        driver_resample=Image.Resampling.LANCZOS,
        session_fills=(
            ("race", red),
            ("qualifying", yellow),
            ("sprint", green),
            ("practice", blue),
            ("default", black),
        ),
        session_shadow_fill=black,
        track_placeholder_fill=black,
        results_split_position_prefix=True,
        include_bold_circuit_location=True,
        use_bold_team_tech_font=len(colors.PALETTE) <= 4,
        qualifying_translation_key="session_qualifying",
        race_translation_key="session_race",
    )
