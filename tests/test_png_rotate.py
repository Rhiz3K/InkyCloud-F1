"""Palette-exact PNG output and 180° rotation: ``format=bmp|png`` and ``rotate=0|180`` (#185 F3)."""

import asyncio
import struct
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

import app.services.renderer_base as renderer_base_module
from app.models import TeamEntry, TeamsData
from app.routes import images
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts, teams_extra
from app.services.artifact_metadata import calendar_identity, write_artifact_metadata
from app.services.bwr_renderer import BwrColors
from app.services.bwry_renderer import BwryColors
from app.services.f1_service import F1Service
from app.services.legal import AttributionMiddleware
from app.services.renderers import display_palette
from app.services.spectra6_renderer import Spectra6Colors
from app.state import clear_bmp_cache, get_bmp_cache, get_bmp_variant_cache
from app.utils import image_conversion
from app.utils.bmp import encode_indexed_bmp_4bit
from app.utils.etag import encode_etag_sidecar, etag_sidecar_path, strong_etag
from app.utils.image_conversion import (
    DEFAULT_IMAGE_OUTPUT,
    ImageOutput,
    OutputFormat,
    Rotation,
    transform_bmp_output,
)

DISPLAYS = ("1bit", "bwr", "bwry", "spectra6")
PALETTES = {
    "1bit": None,
    "bwr": BwrColors.PALETTE,
    "bwry": BwryColors.PALETTE,
    "spectra6": Spectra6Colors.PALETTE,
}
BITS_PER_PIXEL = {"1bit": 1, "bwr": 4, "bwry": 4, "spectra6": 8}
PNG = ImageOutput(OutputFormat.PNG)
ROTATED = ImageOutput(rotate=Rotation.HALF_TURN)
ROTATED_PNG = ImageOutput(OutputFormat.PNG, Rotation.HALF_TURN)
ALL_OUTPUTS = [ImageOutput(fmt, rotate) for fmt in OutputFormat for rotate in Rotation]
# Qatar 2026 (bundled schedule): FP1 2026-11-27 13:30 UTC, lights out 2026-11-29 16:00 UTC.
RACE_START = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
RACE_WEEK = RACE_START - timedelta(hours=20)
OFF_WEEK = datetime(2026, 11, 22, 9, 30, tzinfo=UTC)


def _qatar() -> dict:
    """Return the bundled Qatar 2026 weekend converted to Europe/Prague."""
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    race = next((r for r in races if r["race_name"] == "Qatar Grand Prix"), None)
    assert race is not None
    return race


def _decode(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        return image.copy()


def _rotated(image: Image.Image) -> Image.Image:
    return image.transpose(Image.Transpose.ROTATE_180)


@pytest.fixture(scope="module")
def calendars() -> dict[str, bytes]:
    """Render one real calendar BMP per palette with the production renderers."""
    race = _qatar()
    historical = F1Service.get_historical_from_static(race["circuit"]["circuitId"])
    return {
        display: bmp_artifacts.render_calendar_bytes(display, "en", race, historical, None, "off")
        for display in DISPLAYS
    }


# --- Transform helper ------------------------------------------------------------------------


def test_image_output_describes_the_response():
    assert DEFAULT_IMAGE_OUTPUT.is_default
    assert DEFAULT_IMAGE_OUTPUT.media_type == "image/bmp"
    assert DEFAULT_IMAGE_OUTPUT.filename("calendar") == "calendar.bmp"
    assert not PNG.is_default and not ROTATED.is_default
    assert PNG.media_type == "image/png" and PNG.filename("teams") == "teams.png"
    assert ROTATED_PNG.cache_token == "format=png:rotate=180"
    assert len({output.cache_token for output in ALL_OUTPUTS}) == 4
    assert [rotation.value for rotation in Rotation] == ["0", "180"]


def test_display_palette_is_the_fixed_panel_palette():
    for display in DISPLAYS:
        assert display_palette(display) == PALETTES[display]
    assert display_palette("unknown") is None


def test_default_output_returns_the_same_bytes(calendars):
    for display, bmp in calendars.items():
        assert transform_bmp_output(bmp, palette=PALETTES[display]) is bmp
        assert transform_bmp_output(bmp, DEFAULT_IMAGE_OUTPUT) is bmp


@pytest.mark.parametrize("display", DISPLAYS)
def test_png_keeps_the_pixel_indices_and_the_panel_palette(calendars, display):
    bmp = calendars[display]
    png = transform_bmp_output(bmp, PNG, palette=PALETTES[display])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    source, decoded = _decode(bmp), _decode(png)
    assert decoded.size == source.size == (800, 480)
    assert decoded.mode == source.mode == ("1" if display == "1bit" else "P")
    # Identical palette indices: nothing converted to L/RGB, nothing dithered.
    assert decoded.tobytes() == source.tobytes()
    if display != "1bit":
        palette = PALETTES[display]
        assert decoded.getpalette() == [channel for color in palette for channel in color]
        used = {index for _, index in decoded.getcolors()}
        assert used <= set(range(len(palette)))
    # Measured: 1bit 48 KB -> ~10 KB, bwr/bwry 192 KB -> ~13 KB, spectra6 385 KB -> ~14 KB.
    assert len(png) * 4 < len(bmp)
    assert len(png) < 16_000


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_rotation_is_an_exact_half_turn(calendars, display, output_format):
    bmp = calendars[display]
    rotated = transform_bmp_output(
        bmp, ImageOutput(output_format, Rotation.HALF_TURN), palette=PALETTES[display]
    )
    source, decoded = _decode(bmp), _decode(rotated)
    assert decoded.size == (800, 480)
    assert decoded.tobytes() == _rotated(source).tobytes()
    for x, y in [(0, 0), (799, 0), (17, 300), (400, 240), (799, 479)]:
        assert decoded.getpixel((799 - x, 479 - y)) == source.getpixel((x, y))
    # Rotating twice is the identity at pixel level.
    assert _rotated(decoded).tobytes() == source.tobytes()


@pytest.mark.parametrize("display", DISPLAYS)
def test_rotated_bmp_keeps_the_default_encoding(calendars, display):
    bmp = calendars[display]
    rotated = transform_bmp_output(bmp, ROTATED, palette=PALETTES[display])
    offset = struct.unpack_from("<I", bmp, 10)[0]
    assert struct.unpack_from("<H", rotated, 28)[0] == BITS_PER_PIXEL[display]
    # Same header, colour table and size: 1-bit, 4-bit BWR/BWRY and 8-bit Spectra 6 parsers
    # see exactly the default layout, only the pixel rows differ.
    assert len(rotated) == len(bmp)
    assert rotated[:offset] == bmp[:offset]
    assert rotated != bmp
    assert transform_bmp_output(rotated, ROTATED, palette=PALETTES[display]) == bmp


def test_png_rejects_screens_that_do_not_match_the_panel_palette():
    indexed = Image.new("P", (4, 2), 1)
    indexed.putpalette([0, 0, 0, 255, 255, 255, 255, 0, 0])
    bmp = encode_indexed_bmp_4bit(indexed, BwrColors.PALETTE)
    assert _decode(transform_bmp_output(bmp, PNG, palette=BwrColors.PALETTE)).getpixel((0, 0)) == 1
    with pytest.raises(ValueError, match="needs its panel palette"):
        transform_bmp_output(bmp, PNG)
    with pytest.raises(ValueError, match="does not match"):
        transform_bmp_output(bmp, PNG, palette=BwryColors.PALETTE[1:])
    indexed.putpixel((3, 1), 3)
    outside = encode_indexed_bmp_4bit(indexed, BwryColors.PALETTE)
    with pytest.raises(ValueError, match="outside the panel palette"):
        transform_bmp_output(outside, PNG, palette=BwryColors.PALETTE[:3])


def test_png_rejects_non_indexed_screens():
    buffer = BytesIO()
    Image.new("RGB", (4, 2), (1, 2, 3)).save(buffer, format="BMP")
    with pytest.raises(ValueError, match="Unsupported screen image mode"):
        transform_bmp_output(buffer.getvalue(), PNG)


def test_rotated_bmp_refuses_a_layout_it_cannot_reproduce():
    indexed = Image.new("P", (4, 2), 0)
    bmp = bytearray(encode_indexed_bmp_4bit(indexed, BwrColors.PALETTE))
    bmp[6:8] = b"\x01\x00"  # reserved header bytes written by a foreign encoder
    with pytest.raises(ValueError, match="does not match the source layout"):
        transform_bmp_output(bytes(bmp), ROTATED)


def test_png_uses_the_measured_compression_level(monkeypatch, calendars):
    seen = []
    original = Image.Image.save

    def save(self, fp, *args, **params):
        seen.append((params.get("format"), {k: v for k, v in params.items() if k != "format"}))
        return original(self, fp, *args, **params)

    monkeypatch.setattr(Image.Image, "save", save)
    transform_bmp_output(calendars["1bit"], PNG)
    assert seen == [("PNG", {"compress_level": image_conversion.PNG_COMPRESS_LEVEL})]
    assert image_conversion.PNG_COMPRESS_LEVEL == 9


# --- Variant artifacts ---------------------------------------------------------------------


@pytest.fixture
def clean_caches():
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()
    yield
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()


@pytest.mark.asyncio
async def test_output_artifact_derives_once_into_the_variant_cache(clean_caches, calendars):
    bmp = calendars["bwry"]
    artifact = (bmp, strong_etag(bmp))
    assert await bmp_artifacts.output_artifact(artifact, "bwry", DEFAULT_IMAGE_OUTPUT) == (
        artifact,
        True,
    )
    derived, cached = await bmp_artifacts.output_artifact(artifact, "bwry", ROTATED_PNG)
    assert not cached
    assert derived == (
        transform_bmp_output(bmp, ROTATED_PNG, palette=BwryColors.PALETTE),
        strong_etag(derived[0]),
    )
    key = bmp_artifacts.output_cache_key(artifact, "bwry", ROTATED_PNG)
    assert key == f"output:format=png:rotate=180:bwry:{artifact[1]}"
    assert dict(get_bmp_variant_cache()) == {key: derived}
    assert not get_bmp_cache()
    assert await bmp_artifacts.output_artifact(artifact, "bwry", ROTATED_PNG) == (derived, True)
    other, _ = await bmp_artifacts.output_artifact(artifact, "bwry", PNG)
    assert other != derived and len(get_bmp_variant_cache()) == 2


@pytest.mark.asyncio
async def test_output_artifact_runs_on_the_render_executor_once(
    clean_caches, calendars, monkeypatch
):
    calls = []
    original = bmp_artifacts.run_render

    async def run_render(func):
        calls.append(func)
        await asyncio.sleep(0)
        return await original(func)

    monkeypatch.setattr(bmp_artifacts, "run_render", run_render)
    artifact = (calendars["spectra6"], strong_etag(calendars["spectra6"]))
    results = await asyncio.gather(
        *(bmp_artifacts.output_artifact(artifact, "spectra6", PNG) for _ in range(3))
    )
    assert len(calls) == 1
    assert len({result[0] for result in results}) == 1


@pytest.mark.asyncio
async def test_output_artifact_skips_the_cache_after_a_cache_reset(
    clean_caches, calendars, monkeypatch
):
    generations = [1, 2]
    monkeypatch.setattr(bmp_artifacts, "bmp_cache_generation", lambda: generations.pop(0))
    artifact = (calendars["1bit"], strong_etag(calendars["1bit"]))
    derived, cached = await bmp_artifacts.output_artifact(artifact, "1bit", PNG)
    assert not cached and derived[0].startswith(b"\x89PNG")
    assert not get_bmp_variant_cache()


# --- Routes ----------------------------------------------------------------------------------


class _Clock:
    now = RACE_WEEK


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def image_app(monkeypatch, clean_caches):
    """Mount the image routes with a fixed next race, clock and teams data."""
    _Clock.now = RACE_WEEK
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    race = _qatar()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _qatar())
    record = MagicMock()
    monkeypatch.setattr(images, "record_api_call", record)
    monkeypatch.setattr(images, "enforce_rate_limit", MagicMock())
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(images, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(images, "points_session_starts", lambda year: [RACE_START])
    monkeypatch.setattr(
        images,
        "build_teams_extra_context",
        lambda extra, year, **kwargs: teams_extra.build_teams_extra_context(
            extra, year, now=_Clock.now, **kwargs
        ),
    )
    monkeypatch.setattr(bmp_artifacts, "is_teams_data_cacheable", lambda data: True)
    monkeypatch.setattr(
        bmp_artifacts.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(
            return_value=TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
        ),
    )
    app = FastAPI()
    app.include_router(images.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    app.state.record = record
    return app


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


def _assert_final_bytes(response, media_type: str, filename: str) -> None:
    """Content-Type, Content-Length, ETag and filename describe the bytes actually sent."""
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["etag"] == strong_etag(response.content)
    assert response.headers["content-disposition"] == f'inline; filename="{filename}"'


@pytest.mark.asyncio
async def test_default_calendar_bytes_and_cache_entry_are_unchanged(image_app):
    default = await _get(image_app, "/calendar.bmp?display=bwr")
    explicit = await _get(image_app, "/calendar.bmp?display=bwr&format=bmp&rotate=0")
    _assert_final_bytes(default, "image/bmp", "calendar.bmp")
    assert explicit.content == default.content
    assert explicit.headers["etag"] == default.headers["etag"]
    assert (default.headers["x-cache"], explicit.headers["x-cache"]) == ("MISS", "HIT")
    assert len(get_bmp_cache()) == 1 and not get_bmp_variant_cache()
    assert all("format=" not in key for key in get_bmp_cache())


@pytest.mark.asyncio
@pytest.mark.parametrize("display", DISPLAYS)
async def test_calendar_png_matches_the_default_bmp_pixels(image_app, display):
    bmp = await _get(image_app, f"/calendar.bmp?display={display}")
    png = await _get(image_app, f"/calendar.bmp?display={display}&format=png")
    _assert_final_bytes(png, "image/png", "calendar.png")
    assert _decode(png.content).tobytes() == _decode(bmp.content).tobytes()
    assert png.headers["etag"] != bmp.headers["etag"]
    assert png.headers["cache-control"] == bmp.headers["cache-control"] == "public, max-age=3600"
    assert png.headers["link"] == bmp.headers["link"]
    assert png.headers["x-next-refresh-seconds"] == bmp.headers["x-next-refresh-seconds"]
    assert png.headers["x-cache"] == "MISS"
    rotated = await _get(image_app, f"/calendar.bmp?display={display}&rotate=180")
    _assert_final_bytes(rotated, "image/bmp", "calendar.bmp")
    assert rotated.content == transform_bmp_output(bmp.content, ROTATED, palette=PALETTES[display])
    both = await _get(image_app, f"/calendar.bmp?display={display}&format=png&rotate=180")
    _assert_final_bytes(both, "image/png", "calendar.png")
    assert _decode(both.content).tobytes() == _rotated(_decode(bmp.content)).tobytes()
    # Rendered once: every variant is derived from the one default cache entry.
    assert len(get_bmp_cache()) == 1
    assert sorted(key.split(":")[1:3] for key in get_bmp_variant_cache()) == [
        ["format=bmp", "rotate=180"],
        ["format=png", "rotate=0"],
        ["format=png", "rotate=180"],
    ]
    again = await _get(image_app, f"/calendar.bmp?display={display}&format=png")
    assert again.headers["x-cache"] == "HIT" and again.content == png.content


@pytest.mark.asyncio
async def test_each_variant_revalidates_with_its_own_etag(image_app):
    urls = [
        "/calendar.bmp",
        "/calendar.bmp?format=png",
        "/calendar.bmp?rotate=180",
        "/calendar.bmp?format=png&rotate=180",
    ]
    responses = [await _get(image_app, url) for url in urls]
    etags = [response.headers["etag"] for response in responses]
    assert len(set(etags)) == 4
    for url, etag in zip(urls, etags, strict=True):
        for other in etags:
            response = await _get(image_app, url, {"If-None-Match": other})
            assert response.status_code == (304 if other == etag else 200)
            assert response.headers["etag"] == etag
            if response.status_code == 304:
                assert response.content == b""
                assert "content-type" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/calendar.bmp?format=jpeg",
        "/calendar.bmp?format=PNG",
        "/calendar.bmp?rotate=90",
        "/calendar.bmp?rotate=270",
        "/teams.bmp?rotate=45",
        "/teams.bmp?format=gif",
        "/auto.bmp?rotate=-180",
        "/auto.bmp?format=",
    ],
)
async def test_invalid_format_or_rotation_returns_422(image_app, url):
    response = await _get(image_app, url)
    assert response.status_code == 422
    image_app.state.record.assert_not_called()


@pytest.mark.asyncio
async def test_variants_combine_with_countdown_weekend_and_temperature(image_app, monkeypatch):
    weather = {"off": None, "current": None, "race": None}
    monkeypatch.setattr(bmp_artifacts.config, "WEATHER_ENABLED", True)

    async def context(race_data, *, include_weekend=False, wanted=()):
        return None, None, {**weather, **({"weekend": None} if include_weekend else {})}

    monkeypatch.setattr(bmp_artifacts, "get_weather_context", context)
    params = "display=spectra6&countdown=days&weekend_mode=auto&temp_unit=f&weather_type=weekend"
    base = await _get(image_app, f"/calendar.bmp?{params}")
    variant = await _get(image_app, f"/calendar.bmp?{params}&format=png&rotate=180")
    _assert_final_bytes(variant, "image/png", "calendar.png")
    assert _decode(variant.content).tobytes() == _rotated(_decode(base.content)).tobytes()
    assert variant.headers["x-next-refresh-seconds"] == base.headers["x-next-refresh-seconds"]
    assert not get_bmp_cache()
    assert len(get_bmp_variant_cache()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["none", "flags"])
async def test_teams_variants_keep_the_licence_link(image_app, extra):
    base = await _get(image_app, f"/teams.bmp?display=bwr&extra={extra}")
    png = await _get(image_app, f"/teams.bmp?display=bwr&extra={extra}&format=png&rotate=180")
    _assert_final_bytes(png, "image/png", "teams.png")
    assert _decode(png.content).tobytes() == _rotated(_decode(base.content)).tobytes()
    assert png.headers["link"] == base.headers["link"]
    assert 'rel="license"' in png.headers["link"]
    assert png.headers["cache-control"] == "no-cache"
    assert png.headers["x-cache"] == "MISS"
    revalidated = await _get(
        image_app,
        f"/teams.bmp?display=bwr&extra={extra}&format=png&rotate=180",
        {"If-None-Match": png.headers["etag"]},
    )
    assert revalidated.status_code == 304 and revalidated.headers["x-cache"] == "HIT"


@pytest.mark.asyncio
async def test_auto_serves_the_variant_of_the_active_screen(image_app):
    _Clock.now = RACE_WEEK
    auto = await _get(image_app, "/auto.bmp?display=bwry&format=png&rotate=180")
    direct = await _get(image_app, "/calendar.bmp?display=bwry&format=png&rotate=180")
    assert auto.headers["x-active-screen"] == "calendar"
    _assert_final_bytes(auto, "image/png", "calendar.png")
    assert auto.content == direct.content and auto.headers["etag"] == direct.headers["etag"]
    assert int(auto.headers["x-next-refresh-seconds"]) <= int(
        direct.headers["x-next-refresh-seconds"]
    )
    assert auto.headers["link"] == direct.headers["link"]

    _Clock.now = OFF_WEEK
    teams = await _get(image_app, "/auto.bmp?display=bwry&format=png")
    plain = await _get(image_app, "/teams.bmp?display=bwry&extra=countdown")
    assert teams.headers["x-active-screen"] == "teams"
    _assert_final_bytes(teams, "image/png", "teams.png")
    assert _decode(teams.content).tobytes() == _decode(plain.content).tobytes()
    assert teams.headers["cache-control"] == "no-cache"
    assert image_app.state.record.call_args_list[0].args[0] == "/auto.bmp"


@pytest.mark.asyncio
async def test_attribution_notices_apply_to_png_responses(image_app):
    app = AttributionMiddleware(image_app)
    for url in ("/calendar.bmp?format=png", "/teams.bmp?format=png&rotate=180"):
        response = await _get(app, url)
        assert response.headers["content-type"] == "image/png"
        assert "Jolpica" in response.headers["x-data-attribution"]
        assert 'rel="describedby"' in response.headers["link"]


@pytest.mark.asyncio
async def test_failed_render_returns_the_error_image_in_the_requested_format(
    image_app, monkeypatch
):
    monkeypatch.setattr(
        images, "_get_race_data_from_static", MagicMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(
        bmp_artifacts.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    for url, filename in (
        ("/calendar.bmp?display=bwr&format=png&rotate=180", "calendar.png"),
        ("/teams.bmp?display=bwr&format=png&rotate=180", "teams.png"),
    ):
        response = await _get(image_app, url)
        _assert_final_bytes(response, "image/png", filename)
        assert response.headers["cache-control"] == "no-store"
        assert "x-cache" not in response.headers
        assert _decode(response.content).mode == "P"


# --- Pregenerated files ----------------------------------------------------------------------


async def _pregenerated(tmp_path, data: bytes, identity: str):
    path = tmp_path / "pregenerated.bmp"
    path.write_bytes(data)
    await write_artifact_metadata(path, data, identity)
    etag_sidecar_path(path).write_bytes(
        encode_etag_sidecar(path.stat().st_mtime_ns, strong_etag(data))
    )
    return path


@pytest.mark.asyncio
async def test_pregenerated_calendar_is_the_source_of_its_variants(
    image_app, tmp_path, monkeypatch, calendars
):
    bmp = calendars["1bit"]
    path = await _pregenerated(tmp_path, bmp, calendar_identity(_qatar()))
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=path))
    default_etag = strong_etag(bmp)

    default = await _get(image_app, "/calendar.bmp", {"If-None-Match": default_etag})
    assert default.status_code == 304 and default.headers["x-cache"] == "REVALIDATED"

    # The sidecar ETag belongs to the default BMP, so a variant never takes that shortcut.
    png = await _get(image_app, "/calendar.bmp?format=png", {"If-None-Match": default_etag})
    _assert_final_bytes(png, "image/png", "calendar.png")
    assert png.headers["x-cache"] == "MISS"
    assert _decode(png.content).tobytes() == _decode(bmp).tobytes()
    revalidated = await _get(
        image_app, "/calendar.bmp?format=png", {"If-None-Match": png.headers["etag"]}
    )
    assert revalidated.status_code == 304
    assert revalidated.headers["x-cache"] == "HIT"
    assert revalidated.headers["etag"] == png.headers["etag"]
    assert not get_bmp_cache()


@pytest.mark.asyncio
async def test_pregenerated_teams_is_the_source_of_its_variants(
    image_app, tmp_path, monkeypatch, calendars
):
    bmp = calendars["spectra6"]
    path = await _pregenerated(tmp_path, bmp, "teams:2026")
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=path))
    default = await _get(image_app, "/teams.bmp?display=spectra6")
    assert default.headers["x-cache"] == "PREGENERATED" and default.content == bmp
    rotated = await _get(
        image_app, "/teams.bmp?display=spectra6&rotate=180", {"If-None-Match": strong_etag(bmp)}
    )
    _assert_final_bytes(rotated, "image/bmp", "teams.bmp")
    assert rotated.content == transform_bmp_output(bmp, ROTATED)


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", ["calendar", "teams"])
async def test_unencodable_error_output_is_a_controlled_503(image_app, monkeypatch, screen):
    monkeypatch.setattr(
        bmp_artifacts, "output_artifact", AsyncMock(side_effect=ValueError("conversion"))
    )
    response = await _get(image_app, f"/{screen}.bmp?weather=false&format=png&rotate=180")
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert not response.content.startswith(b"BM")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["memory", "disk", "miss"])
@pytest.mark.parametrize("screen", ["calendar", "teams"])
async def test_clear_during_output_requires_revalidation(
    image_app, tmp_path, monkeypatch, calendars, source, screen
):
    url = f"/{screen}.bmp?weather=false"
    if source == "memory":
        assert (await _get(image_app, url)).status_code == 200
    elif source == "disk":
        identity = calendar_identity(_qatar()) if screen == "calendar" else "teams:2026"
        path = await _pregenerated(tmp_path, calendars["1bit"], identity)
        monkeypatch.setattr(
            images, f"_get_pregenerated_{screen}_path", MagicMock(return_value=path)
        )
    derive = bmp_artifacts.output_artifact

    async def clear_during(artifact, display, output):
        clear_bmp_cache()
        return await derive(artifact, display, output)

    monkeypatch.setattr(bmp_artifacts, "output_artifact", clear_during)
    response = await _get(image_app, url + "&format=png&rotate=180")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    assert response.headers["cache-control"] == "no-cache"
    assert int(response.headers["x-next-refresh-seconds"]) <= 300
    assert response.headers["etag"] == strong_etag(response.content)
