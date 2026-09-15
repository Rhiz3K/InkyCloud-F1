"""Documented artwork catalogue and attributed SVG/PNG/BMP export endpoints."""

from __future__ import annotations

from functools import partial
from io import BytesIO
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageColor

from app.config import config
from app.services.circuit_metadata import canonical_circuit_id
from app.services.track_catalog import (
    ARTWORK_VERSION,
    COLORS,
    DISPLAY_COLORS,
    TrackAccent,
    TrackOptions,
    TrackSource,
    TrackStyle,
    load_track_catalog,
    track_credit,
    track_ui_options,
)
from app.services.track_drawing import build_track_svg
from app.services.track_renderer import attribution_headers, credit_url, render_track_png
from app.utils.async_tasks import run_render
from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette
from app.utils.etag import if_none_match_matches, strong_etag
from app.utils.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/tracks", tags=["Track artwork"])
type TrackDisplay = Literal["1bit", "bwr", "bwry", "spectra6"]
type TrackFormat = Literal["svg", "png", "bmp"]


@router.get("")
async def get_track_catalog(lang: str = "en") -> dict:
    """List all reviewed circuits, choices, hardware palettes, defaults and credits."""
    return {
        "version": ARTWORK_VERSION,
        **track_ui_options(lang),
        "displays": DISPLAY_COLORS,
        "colours": COLORS,
        "tracks": load_track_catalog()["tracks"],
        "credits": "/credits",
        "exports": "/api/tracks/{circuit_id}.{svg|png|bmp}",
    }


def _render_export(
    circuit_id: str, image_format: TrackFormat, display: TrackDisplay, options: TrackOptions
) -> bytes:
    """Render an export on the bounded image worker pool using fixed device dimensions."""
    credit_link = credit_url(circuit_id, options)
    if image_format == "svg":
        return build_track_svg(circuit_id, options, display, 494, 257, credit_url=credit_link)
    png = render_track_png(circuit_id, options, display, 494, 271, credit_link)
    if image_format == "png":
        return png
    with Image.open(BytesIO(png)) as image:
        if display == "1bit":
            output = BytesIO()
            image.convert("1", dither=Image.Dither.NONE).save(output, format="BMP")
            return output.getvalue()
        palette = [
            cast(tuple[int, int, int], ImageColor.getrgb(COLORS[name]))
            for name in DISPLAY_COLORS[display]
        ]
        indexed = quantize_to_palette(image.convert("RGB"), palette, len(palette))
        return encode_indexed_bmp_4bit(indexed, palette)


@router.get("/{circuit_id}.{image_format}", response_model=None)
async def get_track_image(
    circuit_id: str,
    image_format: TrackFormat,
    request: Request,
    display: TrackDisplay = "spectra6",
    track_style: TrackStyle = TrackStyle.RELIEF,
    track_source: TrackSource = TrackSource.JULES,
    track_accent: TrackAccent = TrackAccent.ALL,
) -> Response:
    """Export a tightly fitted map with per-file attribution and exact ePaper colours."""
    circuit_id = canonical_circuit_id(circuit_id)
    if track_credit(circuit_id, track_source) is None:
        raise HTTPException(status_code=404, detail="No reviewed circuit outline")
    enforce_rate_limit(request, bucket="track_art", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    options = TrackOptions(track_style, track_source, track_accent)
    content = await run_render(partial(_render_export, circuit_id, image_format, display, options))
    etag = strong_etag(content)
    unchanged = if_none_match_matches(request.headers.get("If-None-Match"), etag)
    media_type = {"svg": "image/svg+xml", "png": "image/png", "bmp": "image/bmp"}[image_format]
    return Response(
        content=b"" if unchanged else content,
        status_code=304 if unchanged else 200,
        media_type=None if unchanged else media_type,
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=3600",
            **attribution_headers(circuit_id, options, standalone=True),
        },
    )


@router.get("/{circuit_id}")
async def get_track_metadata(circuit_id: str) -> dict:
    """Expose both original-file licences and the licence of the adapted artwork."""
    circuit_id = canonical_circuit_id(circuit_id)
    track = next(
        (item for item in load_track_catalog()["tracks"] if item["id"] == circuit_id), None
    )
    if track is None:
        raise HTTPException(status_code=404, detail="Unknown circuit")
    return {
        **track,
        "version": ARTWORK_VERSION,
        "attribution": {source: track_credit(circuit_id, source) for source in TrackSource},
    }
