"""Geometry, ink containment, palette and provenance regressions for the open artwork flow."""

import json
import math
from io import BytesIO
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import resvg_py
from PIL import Image, ImageColor

from app.paths import ASSETS_DIR
from app.services.f1_service import F1Service
from app.services.track_catalog import (
    ACCENTS,
    COLORS,
    DEFAULT_TRACK_OPTIONS,
    DISPLAY_COLORS,
    TrackAccent,
    TrackOptions,
    TrackSource,
    TrackStyle,
    effective_accents,
    load_track_catalog,
    track_credit,
    track_source,
    track_ui_options,
)
from app.services.track_drawing import build_track_svg
from app.services.track_geometry import (
    INK_MARGINS,
    best_rotation,
    convex_hull,
    fit_track,
    source_points,
)
from app.services.track_renderer import (
    attribution_headers,
    credit_url,
    render_track_image,
    render_track_png,
)
from scripts.build_track_catalog import OUTPUT, build_catalog, vector_commands

TRACKS = [track["id"] for track in load_track_catalog()["tracks"]]


@pytest.mark.parametrize("source", TrackSource)
def test_active_calendar_ids_resolve_both_artwork_sources(source):
    """Use provider IDs from bundled calendars, which can differ from catalogue IDs."""
    for year in (2026, 2027):
        for race in F1Service.get_season_from_static(year):
            if race.round:
                assert track_source(race.Circuit.circuitId, source) is not None, (
                    year,
                    race.Circuit.circuitId,
                    source,
                )


@pytest.mark.parametrize("source", TrackSource)
@pytest.mark.parametrize("style", [TrackStyle.RELIEF, TrackStyle.TYPOGRAPHIC])
@pytest.mark.parametrize("display", DISPLAY_COLORS)
def test_las_vegas_provider_alias_renders_identical_artwork(source, style, display):
    """Las Vegas must render real ink under either ID, including lettering and Credits."""
    options = TrackOptions(style, source)
    images = [
        render_track_image({"circuit": {"circuitId": circuit_id}}, 494, 271, display, options)
        for circuit_id in ("vegas", "las_vegas")
    ]
    assert all(image is not None for image in images)
    assert images[0].size == images[1].size
    assert images[0].tobytes() == images[1].tobytes()
    assert len(images[0].getcolors()) > 1
    assert credit_url("vegas", options).endswith(f"/credits#las_vegas-{source}")
    assert build_track_svg("vegas", options, display) == build_track_svg(
        "las_vegas", options, display
    )


def test_reviewed_bundle_is_reproducible_and_complete():
    """Changing an original or losing a licence must break the checked-in catalogue build."""
    assert OUTPUT.read_bytes() == build_catalog()
    assert len(TRACKS) == 26
    assert len(load_track_catalog()["geometries"]) == 52
    assert {frozenset(colors) for colors in ACCENTS.values()} == {
        frozenset(
            color
            for bit, color in enumerate(("red", "yellow", "blue", "green"))
            if subset & (1 << bit)
        )
        for subset in range(16)
    }
    credit = track_credit("marina_bay", TrackSource.COMMONS)
    assert credit["author"] == "Cherkash"
    assert credit["attribution_sources"][0]["author"] == "Sentoan"
    assert credit["artwork_license"] == "CC-BY-SA-4.0"
    assert "commands" not in credit
    assert track_credit("unknown", TrackSource.JULES) is None
    assert track_ui_options("cs")["defaults"] == {
        "style": "relief",
        "source": "jules",
        "accent": "all",
    }


@pytest.mark.parametrize("circuit_id", TRACKS)
@pytest.mark.parametrize("source", TrackSource)
@pytest.mark.parametrize("style", TrackStyle)
def test_every_source_style_fits_its_complete_visible_ink(circuit_id, source, style):
    """Rasterise outside the advertised viewport so clipped outer rings cannot hide."""
    options = TrackOptions(style, source)
    svg = ET.fromstring(build_track_svg(circuit_id, options, "spectra6", 494, 257))
    width, height = int(svg.attrib["width"]), int(svg.attrib["height"])
    assert width <= 494 and height <= 257
    padding = 24
    svg.attrib.update(
        viewBox=f"{-padding} {-padding} {width + 2 * padding} {height + 2 * padding}",
        width=str(width + 2 * padding),
        height=str(height + 2 * padding),
    )
    rendered = resvg_py.svg_to_bytes(
        svg_string=ET.tostring(svg, encoding="unicode"),
        skip_system_fonts=True,
        font_files=[str(ASSETS_DIR / "fonts/TitilliumWeb-Regular.ttf")],
    )
    with Image.open(BytesIO(rendered)) as image:
        alpha = image.getchannel("A")
        alpha.paste(0, (padding, padding, width + padding, height + padding))
        assert alpha.getbbox() is None, (circuit_id, source, style, alpha.getbbox())
    fitted = fit_track(circuit_id, source, style, 494, 257)
    assert fitted.gain >= 1 - 1e-12
    assert -90 <= fitted.angle < 90


@pytest.mark.parametrize("circuit_id", TRACKS)
@pytest.mark.parametrize("source", TrackSource)
@pytest.mark.parametrize("style", [TrackStyle.RELIEF, TrackStyle.CONTOURS])
def test_rotation_beats_an_independent_dense_angle_search(circuit_id, source, style):
    """Compare the analytic optimum against a separate vectorised 0.1-degree oracle."""
    points, reserve = source_points(circuit_id, source)
    points = np.asarray(points)
    if style == TrackStyle.RELIEF:
        points = points @ np.array([[math.sqrt(3) / 2, 0.5], [-math.sqrt(3) / 2, 0.5]])
        reserve *= math.sqrt(1.5)
    hull = np.array(convex_hull(tuple(map(tuple, points))))
    angles = np.radians(np.arange(-90, 90, 0.1))
    x = hull[:, 0, None] * np.cos(angles) - hull[:, 1, None] * np.sin(angles)
    y = hull[:, 0, None] * np.sin(angles) + hull[:, 1, None] * np.cos(angles)
    left, top, right, bottom = INK_MARGINS[style]
    scales = np.minimum(
        (494 - left - right) / (np.ptp(x, axis=0) + 2 * reserve),
        (257 - top - bottom) / (np.ptp(y, axis=0) + 2 * reserve),
    )
    fitted = fit_track(circuit_id, source, style, 494, 257)
    assert fitted.scale >= float(scales.max()) * (1 - 1e-10)


@pytest.mark.parametrize("style", TrackStyle)
@pytest.mark.parametrize("accent", TrackAccent)
@pytest.mark.parametrize("display", DISPLAY_COLORS)
def test_every_style_accent_hardware_combination_uses_only_its_requested_palette(
    style,
    accent,
    display,
):
    """Antialiasing must not leak unsupported hues or replace a requested unavailable accent."""
    options = TrackOptions(style, TrackSource.JULES, accent)
    content = render_track_png(
        "madring", options, display, 494, 271, "https://example.org/credits#madring-jules"
    )
    with Image.open(BytesIO(content)) as image:
        actual = {color for _, color in image.convert("RGB").getcolors(256)}
        allowed = {
            ImageColor.getrgb(COLORS[name])
            for name in ("black", "white", *effective_accents(display, accent))
        }
        assert actual <= allowed
        assert len(actual) >= 2
        assert "ROY Jules" in image.info["Attribution"]
        assert "example.org/credits" in image.info["Credits"]
    if display == "spectra6" and accent == TrackAccent.ALL and style == TrackStyle.RELIEF:
        assert actual == allowed


def test_safe_missing_outline_and_serialised_svg_metadata():
    """Unknown circuits never read old F1 rasters, while exports retain the licence chain."""
    assert render_track_image({"circuit": {"circuitId": "missing"}}, 494, 271, "1bit") is None
    with pytest.raises(ValueError, match="No reviewed"):
        source_points("missing", TrackSource.JULES)
    with pytest.raises(ValueError, match="No reviewed"):
        fit_track("missing", TrackSource.JULES, TrackStyle.RELIEF)
    with pytest.raises(ValueError, match="slot"):
        fit_track("madring", TrackSource.JULES, TrackStyle.RELIEF, 10, 10000)
    options = TrackOptions(TrackStyle.CONTOURS, TrackSource.COMMONS, TrackAccent.MONO)
    svg = ET.fromstring(
        build_track_svg(
            "marina_bay", options, "1bit", credit_url="https://example.org/credits?a=1&b=2"
        )
    )
    metadata = json.loads(svg.find("{http://www.w3.org/2000/svg}metadata").text)
    assert metadata["attribution_sources"][0]["author"] == "Sentoan"
    assert metadata["artwork_license"] == "CC-BY-SA-4.0"
    for display in ("1bit", "bwr"):
        image = render_track_image(
            {"circuit": {"circuitId": "albert_park"}}, 494, 271, display, options
        )
        assert image.mode == ("1" if display == "1bit" else "RGB")
    assert (
        "license" in attribution_headers("madring", DEFAULT_TRACK_OPTIONS, standalone=True)["Link"]
    )
    assert "license" not in attribution_headers("missing", DEFAULT_TRACK_OPTIONS)["Link"]
    assert credit_url("madring", DEFAULT_TRACK_OPTIONS).endswith("/credits#madring-jules")


def test_degenerate_edges_and_import_validation():
    """Handle duplicate vertices, straight segments and exact ties deterministically."""
    points = ((0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0), (0.0, 0.0))
    angle, scale, _, initial = best_rotation(points, 0, 100, 50)
    assert angle == 0 and scale == initial == 10
    assert vector_commands("M0 0 Q5 10 10 0 L0 0 Z")[1][0] == "Q"
    for d in ("M0 0 L1 1", "<svg/>", "M0 0 Z M1 1 Z"):
        with pytest.raises(ValueError):
            vector_commands(d)
