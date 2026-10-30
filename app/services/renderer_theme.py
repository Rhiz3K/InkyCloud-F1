"""Immutable palette and asset policies shared by renderer variants."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from PIL import Image

RGB: TypeAlias = tuple[int, int, int]
Fill: TypeAlias = int | RGB
TrackPreparer: TypeAlias = Callable[[Image.Image, int, int, Any], Image.Image]


def nearest_palette_color(color: RGB, palette: Sequence[RGB]) -> RGB:
    """Return the palette entry closest to ``color`` by squared RGB distance."""
    return min(
        palette,
        key=lambda entry: sum((a - b) ** 2 for a, b in zip(entry, color, strict=True)),
    )


def team_badge_palette_colors(
    identity: RGB | None,
    palette: Sequence[RGB],
    *,
    black: RGB,
    white: RGB,
) -> tuple[RGB, RGB]:
    """Map a team identity color to a palette fill and a contrasting black/white text fill."""
    if identity is None:
        return black, white
    fill = nearest_palette_color(identity, palette)
    luminance = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
    return fill, black if luminance > 150 else white


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
    team_outline_fill: Fill
    team_panel_fill: Fill
    team_panel_outline_fill: Fill
    team_position_fill: Callable[[int | None], Fill]
    team_points_fill: Fill
    driver_badge_colors: Callable[[int | None], tuple[Fill, Fill]]
    team_badge_colors: Callable[[RGB | None], tuple[Fill, Fill]]
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
        team_outline_fill=colors.BLACK,
        team_panel_fill=colors.WHITE,
        team_panel_outline_fill=colors.BLACK,
        team_position_fill=lambda _position: colors.BLACK,
        team_points_fill=colors.BLACK,
        driver_badge_colors=lambda position: (
            (colors.BLACK, colors.WHITE) if position in {2, 3} else (colors.WHITE, colors.BLACK)
        ),
        team_badge_colors=lambda _identity: (colors.BLACK, colors.WHITE),
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
        team_badge_colors=lambda identity: team_badge_palette_colors(
            identity, colors.PALETTE, black=black, white=white
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
