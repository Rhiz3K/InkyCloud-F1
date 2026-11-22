"""Immutable palette and asset policies shared by renderer variants."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from PIL import Image

RGB: TypeAlias = tuple[int, int, int]
Fill: TypeAlias = int | RGB
TrackPreparer: TypeAlias = Callable[[Image.Image, int, int, Any], Image.Image]


class ThemeMode(StrEnum):
    """Canvas theme of a rendered screen (``theme`` query parameter)."""

    LIGHT = "light"
    DARK = "dark"


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
    canvas_fill: Fill
    # Dark-theme additions; the light defaults draw nothing extra, so light output is unchanged.
    dark: bool = False
    invert_logo: bool = False
    header_rule_fill: Fill | None = None
    team_header_rule_fill: Fill | None = None
    flag_outline_fill: Fill | None = None
    panel_block_fill: Fill | None = None
    panel_block_text_fill: Fill | None = None

    @property
    def block_fill(self) -> Fill:
        """Fill of an emphasised block in a teams panel (the team header colour when light)."""
        return self.team_header_fill if self.panel_block_fill is None else self.panel_block_fill

    @property
    def block_text_fill(self) -> Fill:
        """Text fill on an emphasised teams-panel block."""
        if self.panel_block_text_fill is None:
            return self.team_header_text_fill
        return self.panel_block_text_fill


def make_monochrome_theme(
    *,
    colors: Any,
    track_directory: Callable[[], Path],
    flags_directories: Callable[[], Path | tuple[Path, ...]],
    images_directory: Callable[[], Path],
    prepare_track_image: TrackPreparer,
    dark: bool = False,
) -> RenderTheme:
    """Build the fixed one-bit rendering policy.

    ``dark`` swaps the two inks: a black canvas with white text, lines and highlights.
    """
    ink, paper = (colors.WHITE, colors.BLACK) if dark else (colors.BLACK, colors.WHITE)
    return RenderTheme(
        colors=colors,
        track_directory=track_directory,
        flags_directories=flags_directories,
        images_directory=images_directory,
        prepare_track_image=prepare_track_image,
        normalize_track_image=lambda image: image,
        prepare_flag_image=lambda image: image.copy(),
        header_left_fill=paper,
        header_divider_fill=ink,
        # The title band stays black in both themes; dark separates it with a rule instead.
        header_right_fill=colors.BLACK,
        header_text_fill=colors.WHITE,
        text_fill=ink,
        accent_fill=ink,
        countdown_fill=ink,
        countdown_outline_fill=ink,
        countdown_text_fill=paper,
        error_title_fill=ink,
        error_text_fill=ink,
        team_header_fill=colors.BLACK,
        team_header_text_fill=colors.WHITE,
        team_outline_fill=ink,
        team_panel_fill=paper,
        team_panel_outline_fill=ink,
        team_position_fill=lambda _position: ink,
        team_points_fill=ink,
        driver_badge_colors=lambda position: (ink, paper) if position in {2, 3} else (paper, ink),
        driver_number_fill=ink,
        driver_resample=Image.Resampling.NEAREST,
        session_fills=(
            ("race", ink),
            ("qualifying", ink),
            ("sprint", ink),
            ("practice", ink),
            ("default", ink),
        ),
        session_shadow_fill=None,
        track_placeholder_fill=ink,
        results_split_position_prefix=False,
        include_bold_circuit_location=False,
        use_bold_team_tech_font=False,
        qualifying_translation_key="qualifying",
        race_translation_key="race",
        canvas_fill=paper,
        **_dark_extras(colors, dark, header_rule=True),
    )


def _dark_extras(colors: Any, dark: bool, *, header_rule: bool = False) -> dict[str, Any]:
    """Return the fields only a dark theme sets: rules, outlines and inverted blocks.

    ``header_rule`` underlines a header whose title band is as black as the dark canvas.
    """
    if not dark:
        return {}
    return {
        "dark": True,
        "header_rule_fill": colors.WHITE if header_rule else None,
        "invert_logo": True,
        "team_header_rule_fill": colors.WHITE,
        "flag_outline_fill": colors.WHITE,
        "panel_block_fill": colors.WHITE,
        "panel_block_text_fill": colors.BLACK,
    }


def make_color_theme(
    *,
    colors: Any,
    track_directory: Callable[[], Path],
    flags_directories: Callable[[], Path | tuple[Path, ...]],
    images_directory: Callable[[], Path],
    prepare_track_image: TrackPreparer,
    dark: bool = False,
) -> RenderTheme:
    """Build the common RGB-composition policy for color E-Ink palettes.

    ``dark`` swaps black and white only: red, yellow, green and blue keep their roles, so
    red stays red on a black canvas (no naive inversion to cyan).
    """
    black = colors.BLACK
    white = colors.WHITE
    ink, paper = (white, black) if dark else (black, white)
    red = colors.RED
    # Palettes without an ink fall back to the text colour (black on light, white on dark).
    yellow = getattr(colors, "YELLOW", ink)
    green = getattr(colors, "GREEN", ink)
    blue = getattr(colors, "BLUE", ink)
    return RenderTheme(
        colors=colors,
        track_directory=track_directory,
        flags_directories=flags_directories,
        images_directory=images_directory,
        prepare_track_image=prepare_track_image,
        normalize_track_image=lambda image: image.convert("RGB"),
        prepare_flag_image=lambda image: image.convert("RGB"),
        header_left_fill=paper,
        header_divider_fill=red,
        header_right_fill=red,
        header_text_fill=white,
        text_fill=ink,
        accent_fill=red,
        countdown_fill=red,
        countdown_outline_fill=red,
        countdown_text_fill=white,
        error_title_fill=red,
        error_text_fill=ink,
        team_header_fill=black,
        team_header_text_fill=white,
        team_outline_fill=ink,
        team_panel_fill=paper,
        team_panel_outline_fill=ink,
        team_position_fill=lambda position: red if position == 1 else ink,
        team_points_fill=ink,
        driver_badge_colors=lambda position: (
            (red, white) if position == 1 else (ink, paper) if position in {2, 3} else (paper, ink)
        ),
        driver_number_fill=ink,
        driver_resample=Image.Resampling.LANCZOS,
        session_fills=(
            ("race", red),
            ("qualifying", yellow),
            ("sprint", green),
            ("practice", blue),
            ("default", ink),
        ),
        # The hard shadow only helps light inks on white paper.
        session_shadow_fill=None if dark else black,
        track_placeholder_fill=ink,
        results_split_position_prefix=True,
        include_bold_circuit_location=True,
        use_bold_team_tech_font=len(colors.PALETTE) <= 4,
        qualifying_translation_key="session_qualifying",
        race_translation_key="session_race",
        canvas_fill=paper,
        **_dark_extras(colors, dark),
    )
