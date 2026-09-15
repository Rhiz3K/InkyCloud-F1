"""Rasterise attributed local SVG artwork into exact ePaper palettes."""

from __future__ import annotations

import json
from functools import lru_cache
from io import BytesIO
from typing import cast

import resvg_py
from PIL import Image, ImageColor, PngImagePlugin

from app.config import config
from app.paths import ASSETS_DIR
from app.services.circuit_metadata import canonical_circuit_id
from app.services.track_catalog import (
    COLORS,
    DEFAULT_TRACK_OPTIONS,
    TrackOptions,
    effective_accents,
    track_credit,
    track_source,
)
from app.services.track_drawing import build_track_svg
from app.utils.bmp import preserve_neutral_colors, quantize_to_palette


def credit_url(circuit_id: str, options: TrackOptions) -> str:
    """Link to durable per-file credits using the configured canonical origin."""
    circuit_id = canonical_circuit_id(circuit_id)
    return f"{config.SITE_URL.rstrip('/')}/credits#{circuit_id}-{options.source}"


@lru_cache(maxsize=128)
def render_track_png(
    circuit_id: str,
    options: TrackOptions,
    display: str,
    width: int,
    height: int,
    credit_link: str,
    *,
    show_credit: bool = True,
) -> bytes:
    """Cache PNG bytes with authorship metadata and an optional visible credit row."""
    svg = build_track_svg(
        circuit_id,
        options,
        display,
        width,
        height - 14 if show_credit else height,
        credit_url=credit_link if show_credit else "",
    )
    fonts = [str(ASSETS_DIR / "fonts" / "TitilliumWeb-Regular.ttf")]
    credit = track_credit(circuit_id, options.source)
    assert credit is not None
    if any(ord(character) > 0x2FFF for character in credit["author"]):
        fonts.append(str(ASSETS_DIR / "fonts" / "NotoSansCJK-Regular.ttc"))
    raw_png = resvg_py.svg_to_bytes(
        svg_string=svg.decode(),
        skip_system_fonts=True,
        font_files=fonts,
        font_family="Titillium Web",
    )
    palette = [
        cast(tuple[int, int, int], ImageColor.getrgb(COLORS[color]))
        for color in ("black", "white", *effective_accents(display, options.accent))
    ]
    with Image.open(BytesIO(raw_png)) as raw:
        rgb = preserve_neutral_colors(raw.convert("RGB"), threshold=180)
        indexed = quantize_to_palette(rgb, palette, len(palette))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("Attribution", json.dumps(credit, ensure_ascii=False))
    metadata.add_itxt("Credits", credit_link)
    output = BytesIO()
    indexed.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def render_track_image(
    race_data: dict,
    width: int,
    height: int,
    display: str,
    options: TrackOptions = DEFAULT_TRACK_OPTIONS,
) -> Image.Image | None:
    """Return a map without a credit row for calendars; unknown tracks get a placeholder."""
    circuit_id = canonical_circuit_id(race_data.get("circuit", {}).get("circuitId", ""))
    if track_source(circuit_id, options.source) is None:
        return None
    content = render_track_png(
        circuit_id,
        options,
        display,
        width,
        height,
        credit_url(circuit_id, options),
        show_credit=False,
    )
    with Image.open(BytesIO(content)) as image:
        return (
            image.convert("1", dither=Image.Dither.NONE)
            if display == "1bit"
            else image.convert("RGB")
        )


def attribution_headers(
    circuit_id: str, options: TrackOptions, *, standalone: bool = False
) -> dict[str, str]:
    """Attach machine-readable licensing to calendar and standalone image responses."""
    credit = track_credit(circuit_id, options.source)
    headers = {"Link": f'<{credit_url(circuit_id, options)}>; rel="describedby"'}
    if credit and standalone:
        headers["Link"] += f', <{credit["artwork_license_url"]}>; rel="license"'
    return headers
