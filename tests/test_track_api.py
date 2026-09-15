"""Public artwork contracts and end-to-end calendar/preview cache isolation."""

import asyncio
from copy import deepcopy
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from app.routes import credits as credit_routes
from app.routes import images, pages, previews, tracks
from app.routes.deps import get_f1_service
from app.services.artifact_metadata import calendar_identity, write_artifact_metadata
from app.services.f1_service import F1Service
from app.services.track_catalog import DEFAULT_TRACK_OPTIONS, TrackOptions, TrackSource, TrackStyle
from app.state import get_bmp_cache
from app.utils.rate_limit import _reset_rate_limit_state_for_tests


@pytest.fixture
def artwork_app(monkeypatch, tmp_path):
    """Use real renderers and local race data without startup jobs or external services."""
    race = deepcopy(F1Service().get_next_race_from_static())
    assert race
    race["circuit"]["circuitId"] = "madring"
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: race)
    monkeypatch.setattr(F1Service, "get_historical_from_static", lambda *_: None)
    monkeypatch.setattr(images.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(images.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(images.config, "IMAGE_RATE_LIMIT_PER_MINUTE", 10000)
    monkeypatch.setattr(images, "_record_calendar_api_call", lambda **_: None)
    monkeypatch.setattr(images, "_schedule_calendar_analytics", lambda **_: None)
    monkeypatch.setattr(pages, "track_pageview", AsyncMock())
    get_bmp_cache().clear()
    _reset_rate_limit_state_for_tests()
    images._singleflight_tasks.clear()
    app = FastAPI()
    for router in (
        tracks.router,
        images.router,
        previews.router,
        credit_routes.router,
        pages.router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_f1_service] = F1Service
    yield app
    get_bmp_cache().clear()
    _reset_rate_limit_state_for_tests()
    images._singleflight_tasks.clear()


def test_catalog_metadata_and_openapi_share_all_public_choices(artwork_app):
    """Advertised defaults and counts match the parameters accepted by FastAPI."""
    client = TestClient(artwork_app)
    catalog = client.get("/api/tracks?lang=cs").json()
    assert catalog["defaults"] == {"style": "relief", "source": "jules", "accent": "all"}
    assert [len(catalog[key]) for key in ("tracks", "styles", "sources", "accents")] == [
        26,
        18,
        2,
        16,
    ]
    metadata = client.get("/api/tracks/marina_bay").json()["attribution"]["commons"]
    assert metadata["attribution_sources"][0]["author"] == "Sentoan"
    assert metadata["artwork_license"] == "CC-BY-SA-4.0"
    assert client.get("/api/tracks/missing").status_code == 404
    schema = client.get("/openapi.json").json()
    assert "/credits" not in schema["paths"]
    assert "/configure/{screen_type}" not in schema["paths"]
    for path in (
        "/calendar.bmp",
        "/preview/configure/{screen_type}.png",
        "/api/tracks/{circuit_id}.{image_format}",
    ):
        params = {p["name"]: p for p in schema["paths"][path]["get"]["parameters"]}
        assert params["track_style"]["schema"]["default"] == "relief"
        assert params["track_accent"]["schema"]["default"] == "all"
        assert params["track_source"]["schema"]["default"] == "jules"


@pytest.mark.parametrize("source", TrackSource)
@pytest.mark.parametrize("suffix", ["svg", "png", "bmp"])
def test_las_vegas_api_alias_matches_canonical_exports(artwork_app, source, suffix):
    """Provider aliases share map bytes, validators, metadata and working credit anchors."""
    client = TestClient(artwork_app)
    response = client.get(f"/api/tracks/vegas.{suffix}", params={"track_source": source})
    canonical = client.get(f"/api/tracks/las_vegas.{suffix}", params={"track_source": source})
    assert response.status_code == canonical.status_code == 200
    assert response.content == canonical.content
    assert response.headers["etag"] == canonical.headers["etag"]
    assert f"/credits#las_vegas-{source}" in response.headers["link"]
    assert client.get("/api/tracks/vegas").json() == client.get("/api/tracks/las_vegas").json()
    assert client.get("/api/tracks/vegas").json()["id"] == "las_vegas"
    assert f'id="las_vegas-{source}"' in client.get("/credits").text
    cached = client.get(
        f"/api/tracks/vegas.{suffix}",
        params={"track_source": source},
        headers={"If-None-Match": canonical.headers["etag"]},
    )
    assert cached.status_code == 304


@pytest.mark.parametrize("source", TrackSource)
def test_las_vegas_calendar_and_preview_contain_track_art(artwork_app, monkeypatch, source):
    """Exercise the actual Las Vegas race through both full calendar output formats."""
    service = F1Service()
    race = next(
        (
            race
            for race in service.get_season_from_static(2026)
            if race.Circuit.circuitId == "vegas"
        ),
        None,
    )
    assert race is not None
    selected = service._convert_race_times(race)
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda _: selected)
    client = TestClient(artwork_app)
    bmp = client.get(
        "/calendar.bmp", params={"weather": "false", "display": "spectra6", "track_source": source}
    )
    png = client.get(
        "/preview/configure/calendar.png",
        params={"weather_type": "off", "display": "spectra6", "track_source": source},
    )
    assert bmp.status_code == png.status_code == 200
    assert f"/credits#las_vegas-{source}" in bmp.headers["link"]
    with Image.open(BytesIO(bmp.content)) as calendar, Image.open(BytesIO(png.content)) as preview:
        assert calendar.size == preview.size == (800, 480)
        assert calendar.convert("RGB").tobytes() == preview.convert("RGB").tobytes()
        # Stay inside the empty placeholder border, away from labels and other panels.
        track_area = calendar.convert("RGB").crop((40, 130, 460, 320))
        assert ImageChops.difference(
            track_area, Image.new("RGB", track_area.size, "white")
        ).getbbox()


@pytest.mark.parametrize("suffix", ["svg", "png", "bmp"])
@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
def test_standalone_exports_are_attributed_and_revalidate(artwork_app, suffix, display):
    """Conditional exports retain their licence headers and correct binary formats."""
    client = TestClient(artwork_app)
    url = f"/api/tracks/madring.{suffix}?display={display}&track_source=commons"
    response = client.get(url)
    assert response.status_code == 200
    assert 'rel="license"' in response.headers["link"]
    assert "by-sa/3.0" in response.headers["link"]
    if suffix == "svg":
        assert b"GabrielStella" in response.content
    else:
        with Image.open(BytesIO(response.content)) as image:
            assert image.width <= 494 and image.height <= 271
            assert image.format == suffix.upper()
    cached = client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""
    assert cached.headers["link"] == response.headers["link"]


@pytest.mark.parametrize(
    "base", ["/calendar.bmp", "/preview/configure/calendar.png", "/api/tracks/madring.svg"]
)
@pytest.mark.parametrize("parameter", ["track_style", "track_source", "track_accent"])
def test_invalid_artwork_options_never_silently_select_a_default(artwork_app, base, parameter):
    """Reject invalid enums before rendering, including path and URL injection strings."""
    client = TestClient(artwork_app)
    response = client.get(base, params={parameter: "../../https://example.org/input.svg"})
    assert response.status_code == 422
    assert client.get("/api/tracks/unknown.png").status_code == 404
    assert client.get("/api/tracks/madring.exe").status_code == 422


@pytest.mark.asyncio
async def test_concurrent_calendar_choices_and_png_preview_keep_their_identity(artwork_app):
    """Different source/style requests cannot share an in-flight result or cached default."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=artwork_app), base_url="http://test"
    ) as client:
        paths = [
            "/calendar.bmp?weather=false&display=spectra6",
            "/calendar.bmp?weather=false&display=spectra6&track_style=contours&track_source=commons",
        ]
        first, second = await asyncio.gather(*(client.get(path) for path in paths))
        assert first.status_code == second.status_code == 200
        assert first.content != second.content
        assert first.headers["etag"] != second.headers["etag"]
        assert "madring-jules" in first.headers["link"]
        assert "madring-commons" in second.headers["link"]
        assert 'rel="license"' not in first.headers["link"]  # Separate database/graphic licences.
        repeat = await client.get(paths[1], headers={"If-None-Match": second.headers["etag"]})
        assert repeat.status_code == 304
        preview_url = (
            "/preview/configure/calendar.png?weather_type=off&display=spectra6"
            "&track_style=contours&track_source=commons"
        )
        preview = await client.get(preview_url)
        assert preview.status_code == 200
        with (
            Image.open(BytesIO(second.content)) as bmp,
            Image.open(BytesIO(preview.content)) as png,
        ):
            assert bmp.size == png.size == (800, 480)
            assert bmp.convert("RGB").tobytes() == png.convert("RGB").tobytes()
        conditional = await client.get(
            preview_url, headers={"If-None-Match": preview.headers["etag"]}
        )
        assert conditional.status_code == 304 and conditional.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_options_suffix", ["", ":open-tracks-2-bee3b016e095f88f:relief:jules:all"]
)
async def test_old_raster_identity_and_default_pregeneration_cannot_supply_custom_art(
    artwork_app, tmp_path, old_options_suffix
):
    """Reject old official-map artifacts and never use default bytes for a custom style."""
    path = tmp_path / "calendar_en.bmp"
    path.write_bytes(b"OLD-F1-RASTER")
    race = F1Service().get_next_race_from_static()
    await write_artifact_metadata(
        path, path.read_bytes(), "calendar:" + race["race_key"] + old_options_suffix
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=artwork_app), base_url="http://test"
    ) as client:
        response = await client.get("/calendar.bmp?weather=false")
        assert response.content != b"OLD-F1-RASTER"
        path.write_bytes(b"DEFAULT-ONLY")
        await write_artifact_metadata(path, path.read_bytes(), calendar_identity(race))
        with patch("app.routes.images._get_pregenerated_calendar_path") as pregen:
            response = await client.get("/calendar.bmp?weather=false&track_style=pixel")
            pregen.assert_not_called()
            assert response.content != b"DEFAULT-ONLY"
        assert calendar_identity(race) != calendar_identity(
            race, TrackOptions(TrackStyle.PIXEL, TrackSource.COMMONS)
        )
        assert DEFAULT_TRACK_OPTIONS.cache_key in calendar_identity(race)


@pytest.mark.parametrize("language", [None, *sorted(credit_routes.VALID_LANGUAGES)])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_credits_redirects_only_to_canonical_local_paths(artwork_app, language, method):
    """Locale redirects ignore attacker-controlled host and forwarded headers."""
    client = TestClient(artwork_app)
    path = f"/{language}/credits/" if language else "/credits/"
    expected = f"/{language}/credits" if language not in (None, "en") else "/credits"
    response = client.request(
        method,
        path,
        headers={"Host": "attacker.invalid", "X-Forwarded-Host": "attacker.invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["location"] == expected


@pytest.mark.parametrize(
    "language",
    ["https://attacker.invalid", "//attacker.invalid", "\\\\attacker.invalid", "", "zz"],
)
def test_credits_redirect_rejects_arbitrary_query_destinations(artwork_app, language):
    """A query parameter cannot turn the slash-normalisation route into an open redirect."""
    response = TestClient(artwork_app).get(
        "/credits/", params={"lang_prefix": language}, follow_redirects=False
    )
    assert response.status_code == 404
    assert "location" not in response.headers


def test_credits_and_configuration_work_without_hover_or_javascript(artwork_app):
    """All 52 authorship anchors and controls are present in server-rendered HTML."""
    client = TestClient(artwork_app)
    response = client.get("/cs/credits")
    assert response.status_code == 200
    assert 'id="marina_bay-commons"' in response.text
    assert "Sentoan" in response.text and "ROY Jules" in response.text
    assert response.text.count("Metadata a kontrolní součet originálu") == 52
    assert client.get("/credits").status_code == 200
    assert client.head("/credits").content == b""
    assert client.get("/en/credits", follow_redirects=False).headers["location"] == "/credits"
    assert client.get("/cs/credits/", follow_redirects=False).headers["location"] == "/cs/credits"
    assert client.get("/credits/", follow_redirects=False).headers["location"] == "/credits"
    assert client.get("/zz/credits").status_code == 404
    assert client.get("/zz/credits/").status_code == 404
    config = client.get("/cs/configure/calendar").text
    assert 'id="track-style"' in config and 'value="relief" selected' in config
    assert 'value="all" selected' in config and 'value="jules" selected' in config
    assert "Izometrický reliéf" in config and "appendTrackParams(params)" in config
    assert 'id="track-style"' not in client.get("/configure/teams").text


@pytest.mark.asyncio
async def test_cold_configure_preview_renders_without_default_pregeneration(artwork_app):
    """A custom preview works immediately after startup and shares its completed BMP cache."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=artwork_app), base_url="http://test"
    ) as client:
        url = "/preview/configure/calendar.png?track_style=pixel&weather_type=race&display=bwr"
        response = await client.get(url)
        assert response.status_code == 200
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (800, 480)
        assert len(get_bmp_cache()) == 1
        assert response.headers["cache-control"] == "public, max-age=300"
        get_bmp_cache().clear()
        with patch.object(F1Service, "get_next_race_from_static", return_value=None):
            failed = await client.get(url)
        assert failed.headers["cache-control"] == "no-store"
        assert not get_bmp_cache()
