"""Extended behavioral coverage for calendar and teams image routes."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models import TeamEntry, TeamsData
from app.routes import images
from app.services.image_keys import get_teams_image_key
from app.services.weather_service import WeatherData
from app.state import get_bmp_cache
from app.utils.etag import encode_etag_sidecar, etag_sidecar_path, strong_etag


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/calendar.bmp",
            "raw_path": b"/calendar.bmp",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        }
    )


@pytest.fixture(autouse=True)
def clear_image_cache():
    get_bmp_cache().clear()
    images._singleflight_tasks.clear()
    yield
    get_bmp_cache().clear()
    images._singleflight_tasks.clear()


def test_timezone_validation_and_round_conversion_edge_cases():
    with (
        patch("app.routes.images.is_valid_timezone", return_value=False),
        pytest.raises(HTTPException) as error,
    ):
        images._validate_timezone_param("Bad/Zone")
    assert error.value.status_code == 400

    assert images._to_round_number("") is None
    assert images._to_round_number("bad") is None
    assert images._to_round_number(0) is None
    assert images._to_round_number("2") == 2


def test_race_stats_metadata_covers_invalid_selection_key_and_next_race():
    service = SimpleNamespace(
        get_all_races_from_static=MagicMock(
            return_value=[
                {"race_key": "monza", "race_name": "Italian GP", "round": "15"},
                {"race_name": "Other", "round": "16"},
            ]
        ),
        get_next_race_from_static=MagicMock(
            return_value={"season": "2026", "round": "17", "race_name": "Next GP"}
        ),
    )
    assert images._get_race_info_for_stats(service, None, None, "monza") == (
        False,
        None,
        None,
        None,
    )
    assert images._get_race_info_for_stats(service, 2026, None, "monza") == (
        False,
        2026,
        15,
        "Italian GP",
    )
    assert images._get_race_info_for_stats(service, None, None, None) == (
        True,
        2026,
        17,
        "Next GP",
    )
    assert images._get_race_info_for_stats(service, 2026, 99, None) == (
        False,
        2026,
        99,
        None,
    )
    service.get_next_race_from_static.return_value = None
    assert images._get_race_info_for_stats(service, None, None, None) == (
        True,
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_calendar_analytics_includes_all_selection_fields():
    pageview = AsyncMock()
    event = AsyncMock()
    with (
        patch("app.routes.images.track_pageview", new=pageview),
        patch("app.routes.images.track_event", new=event),
    ):
        await images._track_calendar_analytics(
            lang="en",
            tz="UTC",
            year=2026,
            race_round=2,
            race_key="monza",
            user_agent="test",
            referrer="https://example.test",
        )

    assert pageview.await_args.kwargs["url"] == (
        "/calendar.bmp?lang=en&tz=UTC&year=2026&round=2&race_key=monza"
    )
    assert event.await_args.kwargs["event_data"]["source"] == "referral"


def test_fresh_pregenerated_path_rejects_escape_missing_and_stale_files(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    outside = tmp_path / "outside.bmp"
    outside.touch()
    with patch("app.routes.images.config.IMAGES_PATH", str(images_dir)):
        assert images._fresh_pregenerated_path(outside) is None
        assert images._fresh_pregenerated_path(images_dir / "missing.bmp") is None

        stale = images_dir / "stale.bmp"
        stale.touch()
        old = datetime.now(timezone.utc) - timedelta(hours=7)
        os.utime(stale, (old.timestamp(), old.timestamp()))
        assert images._fresh_pregenerated_path(stale) is None


def test_pregenerated_path_helpers_cover_explicit_invalid_timezone_and_missing_filename(tmp_path):
    assert (
        images._get_pregenerated_calendar_path(
            lang="en",
            year=2026,
            race_round=None,
            race_key=None,
            tz=None,
            display="1bit",
            weather=True,
            weather_type="race",
        )
        is None
    )
    with (
        patch("app.routes.images.config.IMAGES_PATH", str(tmp_path)),
        patch("app.routes.images.normalize_timezone", return_value="Bad/Zone"),
        patch("app.routes.images.is_valid_timezone", return_value=False),
    ):
        assert (
            images._get_pregenerated_calendar_path(
                lang="en",
                year=None,
                race_round=None,
                race_key=None,
                tz="Bad/Zone",
                display="1bit",
                weather=True,
                weather_type="race",
            )
            is None
        )

    with (
        patch("app.routes.images.get_default_teams_year", return_value=2026),
        patch("app.routes.images._get_valid_teams_filenames", return_value={}),
    ):
        assert images._get_pregenerated_teams_path(lang="en", year=None, display="1bit") is None


def test_static_race_selection_covers_key_round_missing_and_next():
    races = [
        {"race_key": "monza", "round": "15"},
        {"race_key": "spa", "round": "14"},
    ]
    service = SimpleNamespace(
        get_all_races_from_static=MagicMock(return_value=races),
        get_next_race_from_static=MagicMock(return_value={"race_key": "next"}),
    )
    assert images._get_race_data_from_static(service, None, None, "monza") is None
    assert images._get_race_data_from_static(service, 2026, None, "monza") == races[0]
    assert images._get_race_data_from_static(service, 2026, 14, None) == races[1]
    assert images._get_race_data_from_static(service, 2026, 1, None) is None
    assert images._get_race_data_from_static(service, None, None, None) == {"race_key": "next"}


def test_timezone_conversion_returns_same_or_converted_payload():
    race = {"timezone": "UTC"}
    assert images._maybe_convert_timezone(race, "UTC") is race
    with patch(
        "app.routes.images.convert_race_times_to_timezone",
        return_value={"timezone": "Europe/Prague"},
    ) as convert:
        assert images._maybe_convert_timezone(race, "Europe/Prague") == {
            "timezone": "Europe/Prague"
        }
    convert.assert_called_once_with(race, "Europe/Prague")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("weather_type", "expected"), [("race_day", "race"), ("current", "current")]
)
async def test_render_calendar_selects_requested_weather(weather_type, expected):
    race = {"circuit": {"circuitId": "monza"}, "timezone": "UTC"}
    values = {
        "race": WeatherData(20.0, 1, 10),
        "current": WeatherData(21.0, 2, 20),
    }
    service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=race))
    run_render = AsyncMock(return_value=b"bmp")
    with (
        patch(
            "app.routes.images.get_weather_context",
            new=AsyncMock(return_value=(None, None, values)),
        ),
        patch("app.routes.images.run_render", new=run_render),
        patch("app.routes.images.F1Service.get_historical_from_static", return_value={}),
        patch("app.routes.images.config.WEATHER_ENABLED", True),
    ):
        result = await images._render_calendar(
            f1_service=service,
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            target_tz="UTC",
            weather=True,
            weather_type=weather_type,
            display="1bit",
        )

    assert result == (b"bmp", race, True)
    assert run_render.await_args.args[0].args[4] is values[expected]


@pytest.mark.asyncio
async def test_render_calendar_ignores_weather_for_off_variant():
    race = {"circuit": {}, "timezone": "UTC"}
    service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=race))
    run_render = AsyncMock(return_value=b"bmp")
    with (
        patch(
            "app.routes.images.get_weather_context", new=AsyncMock(return_value=(None, None, {}))
        ),
        patch("app.routes.images.run_render", new=run_render),
        patch("app.routes.images.config.WEATHER_ENABLED", True),
    ):
        await images._render_calendar(
            f1_service=service,
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            target_tz="UTC",
            weather=True,
            weather_type="off",
            display="1bit",
        )
    assert run_render.await_args.args[0].args[4] is None


@pytest.mark.asyncio
async def test_calendar_endpoint_serves_pregenerated_file_and_error_fallback(tmp_path):
    pregenerated = tmp_path / "calendar.bmp"
    pregenerated.write_bytes(b"pregenerated")
    f1 = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value={"season": 2026, "round": 1}),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=pregenerated),
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images._schedule_calendar_analytics"),
    ):
        response = await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )
    assert response.headers["x-cache"] == "MISS"
    assert any(content == b"pregenerated" for content, _etag in get_bmp_cache().values())

    get_bmp_cache().clear()
    disappeared = MagicMock()
    disappeared.read_bytes.side_effect = OSError("gone")
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=disappeared),
        patch(
            "app.routes.images._render_calendar",
            new=AsyncMock(return_value=(b"rendered", None, False)),
        ),
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images._schedule_calendar_analytics"),
    ):
        response = await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )
    assert response.headers["cache-control"] == "no-store"

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=None),
        patch(
            "app.routes.images._render_calendar", new=AsyncMock(side_effect=RuntimeError("failed"))
        ),
        patch("app.routes.images.run_render", new=AsyncMock(return_value=b"error")),
        patch("app.routes.images.record_api_call"),
        patch("app.routes.images.sentry_sdk.capture_exception") as capture,
    ):
        response = await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )
    assert response.headers["cache-control"] == "no-store"
    capture.assert_called_once()


@pytest.mark.asyncio
async def test_teams_endpoint_serves_cache_and_caches_complete_render():
    request = _request()
    cache_key = "teams_2026_en"
    get_bmp_cache()[cache_key] = (b"cached", strong_etag(b"cached"))
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images.get_default_teams_year", return_value=2026),
        patch("app.routes.images.record_api_call"),
    ):
        response = await images.get_teams_bmp(request, lang="en", year=2026, display="1bit")
    assert response.media_type == "image/bmp"

    get_bmp_cache().clear()
    data = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Team")],
        standings_complete=True,
    )
    service = SimpleNamespace(get_teams_and_drivers=AsyncMock(return_value=data))
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_teams_path", return_value=None),
        patch("app.routes.images.TeamsService", return_value=service),
        patch("app.routes.images.run_render", new=AsyncMock(return_value=b"rendered")),
        patch("app.routes.images.record_api_call"),
    ):
        await images.get_teams_bmp(request, lang="en", year=2026, display="1bit")
    assert get_bmp_cache()[cache_key] == (b"rendered", strong_etag(b"rendered"))


@pytest.mark.asyncio
async def test_calendar_cached_etag_returns_empty_304_without_analytics():
    etag = strong_etag(b"cached")
    cache_key = images._get_cache_key("en", None, None, None, None, True, "race_day", "1bit")
    get_bmp_cache()[cache_key] = (b"cached", etag)
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(
            return_value={"season": 2026, "round": 1, "race_name": "Test GP"}
        ),
        get_all_races_from_static=MagicMock(return_value=[]),
    )

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._record_calendar_api_call") as record_call,
        patch("app.routes.images._schedule_calendar_analytics") as analytics,
    ):
        response = await images.get_calendar_bmp(
            _request({"If-None-Match": etag}),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=service,
        )

    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["etag"] == etag
    assert response.headers["x-cache"] == "HIT"
    assert response.headers["cache-control"] == images.CALENDAR_BMP_CACHE_CONTROL
    record_call.assert_called_once()
    assert record_call.call_args.kwargs["status_code"] == 304
    assert record_call.call_args.kwargs["size_bytes"] == 0
    analytics.assert_not_called()


@pytest.mark.asyncio
async def test_pregenerated_matching_sidecar_skips_bmp_read(tmp_path):
    image_path = tmp_path / "calendar.bmp"
    image_path.write_bytes(b"pregenerated")
    etag = strong_etag(b"pregenerated")
    etag_sidecar_path(image_path).write_bytes(
        encode_etag_sidecar(image_path.stat().st_mtime_ns, etag)
    )

    artifact, matching_etag = await images._read_pregenerated_artifact(
        image_path, strong_etag(b"different")
    )
    assert artifact == (b"pregenerated", etag)
    assert matching_etag is None

    with patch.object(Path, "read_bytes", side_effect=AssertionError("BMP body was read")):
        artifact, matching_etag = await images._read_pregenerated_artifact(image_path, etag)

    assert artifact is None
    assert matching_etag == etag


@pytest.mark.asyncio
async def test_pregenerated_matching_sidecar_revalidates_before_304(tmp_path):
    image_path = tmp_path / "calendar.bmp"
    old_body = b"old-pregenerated"
    new_body = b"new-pregenerated"
    image_path.write_bytes(old_body)
    os.utime(image_path, ns=(1_000_000_000, 1_000_000_000))
    old_etag = strong_etag(old_body)
    new_etag = strong_etag(new_body)
    etag_sidecar_path(image_path).write_bytes(
        encode_etag_sidecar(image_path.stat().st_mtime_ns, old_etag)
    )
    original_read_sidecar = images.read_etag_sidecar
    sidecar_reads = 0

    def replace_after_first_sidecar_read(path: Path) -> str | None:
        nonlocal sidecar_reads
        etag = original_read_sidecar(path)
        sidecar_reads += 1
        if sidecar_reads == 1:
            path.write_bytes(new_body)
            etag_sidecar_path(path).write_bytes(
                encode_etag_sidecar(path.stat().st_mtime_ns, new_etag)
            )
        return etag

    with patch(
        "app.routes.images.read_etag_sidecar",
        side_effect=replace_after_first_sidecar_read,
    ):
        artifact, matching_etag = await images._read_pregenerated_artifact(image_path, old_etag)

    assert artifact == (new_body, new_etag)
    assert matching_etag is None
    assert sidecar_reads == 3


@pytest.mark.asyncio
async def test_pregenerated_hashes_replaced_body_when_sidecar_turns_stale(tmp_path):
    image_path = tmp_path / "calendar.bmp"
    old_body = b"old-pregenerated"
    new_body = b"new-pregenerated"
    image_path.write_bytes(old_body)
    os.utime(image_path, ns=(1_000_000_000, 1_000_000_000))
    old_etag = strong_etag(old_body)
    etag_sidecar_path(image_path).write_bytes(
        encode_etag_sidecar(image_path.stat().st_mtime_ns, old_etag)
    )
    original_read_sidecar = images.read_etag_sidecar
    sidecar_reads = 0

    def replace_body_after_sidecar_read(path: Path) -> str | None:
        nonlocal sidecar_reads
        etag = original_read_sidecar(path)
        sidecar_reads += 1
        if sidecar_reads == 1:
            path.write_bytes(new_body)
        return etag

    with patch(
        "app.routes.images.read_etag_sidecar",
        side_effect=replace_body_after_sidecar_read,
    ):
        artifact, matching_etag = await images._read_pregenerated_artifact(image_path, None)

    assert artifact == (new_body, strong_etag(new_body))
    assert matching_etag is None
    assert sidecar_reads == 2


@pytest.mark.asyncio
async def test_calendar_endpoint_returns_304_from_pregenerated_sidecar(tmp_path):
    image_path = tmp_path / "calendar.bmp"
    image_path.write_bytes(b"pregenerated-calendar")
    etag = strong_etag(b"pregenerated-calendar")
    etag_sidecar_path(image_path).write_bytes(
        encode_etag_sidecar(image_path.stat().st_mtime_ns, etag)
    )
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(
            return_value={"season": 2026, "round": 1, "race_name": "Test GP"}
        ),
        get_all_races_from_static=MagicMock(return_value=[]),
    )

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=image_path),
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images._schedule_calendar_analytics"),
    ):
        response = await images.get_calendar_bmp(
            _request({"If-None-Match": etag}),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=service,
        )

    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["etag"] == etag
    assert response.headers["x-cache"] == "REVALIDATED"


@pytest.mark.asyncio
async def test_teams_endpoint_handles_sidecar_304_and_empty_fallback(tmp_path):
    image_path = tmp_path / "teams.bmp"
    image_path.write_bytes(b"pregenerated-teams")
    etag = strong_etag(b"pregenerated-teams")
    etag_sidecar_path(image_path).write_bytes(
        encode_etag_sidecar(image_path.stat().st_mtime_ns, etag)
    )

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_teams_path", return_value=image_path),
        patch("app.routes.images._record_teams_api_call"),
    ):
        response = await images.get_teams_bmp(
            _request({"If-None-Match": etag}), lang="en", year=2026, display="1bit"
        )

    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["etag"] == etag
    assert response.headers["x-cache"] == "REVALIDATED"

    empty_data = TeamsData(season=2026, teams=[], standings_complete=False)
    service = SimpleNamespace(get_teams_and_drivers=AsyncMock(return_value=empty_data))
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_teams_path", return_value=image_path),
        patch(
            "app.routes.images._read_pregenerated_artifact",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch("app.routes.images.TeamsService", return_value=service),
        patch("app.routes.images.run_render", new=AsyncMock(return_value=b"empty-teams")),
        patch("app.routes.images._record_teams_api_call"),
    ):
        response = await images.get_teams_bmp(_request(), lang="en", year=2026, display="1bit")

    assert response.status_code == 200
    assert response.body == b"empty-teams"
    assert get_bmp_cache().get(get_teams_image_key("en", 2026, display="1bit")) is None


@pytest.mark.asyncio
async def test_changed_teams_content_returns_200_with_new_etag_and_no_cache():
    old_etag = strong_etag(b"old")
    new_etag = strong_etag(b"new")
    cache_key = get_teams_image_key("en", 2026, display="1bit")
    get_bmp_cache()[cache_key] = (b"new", new_etag)

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._record_teams_api_call") as record_call,
    ):
        response = await images.get_teams_bmp(
            _request({"If-None-Match": old_etag}),
            lang="en",
            year=2026,
            display="1bit",
        )

    assert response.status_code == 200
    assert response.body == b"new"
    assert response.headers["etag"] == new_etag
    assert response.headers["cache-control"] == "no-cache"
    assert record_call.call_args.kwargs["status_code"] == 200
    assert record_call.call_args.kwargs["size_bytes"] == 3


@pytest.mark.asyncio
async def test_concurrent_identical_calendar_misses_render_and_hash_once():
    started = asyncio.Event()
    release = asyncio.Event()

    async def render_once(**_kwargs):
        started.set()
        await release.wait()
        return b"rendered", None, True

    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value={"season": 2026, "round": 1}),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    render = AsyncMock(side_effect=render_once)

    async def request_calendar():
        return await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=service,
        )

    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=None),
        patch("app.routes.images._render_calendar", new=render),
        patch("app.routes.images.strong_etag", wraps=strong_etag) as hash_content,
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images._schedule_calendar_analytics"),
    ):
        first = asyncio.create_task(request_calendar())
        await started.wait()
        second = asyncio.create_task(request_calendar())
        await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(first, second)

    assert render.await_count == 1
    assert hash_content.call_count == 1
    assert [response.body for response in responses] == [b"rendered", b"rendered"]
    assert responses[0].headers["etag"] == responses[1].headers["etag"]
    assert images._singleflight_tasks == {}


@pytest.mark.asyncio
async def test_singleflight_propagates_failure_and_cleans_up():
    async def fail():
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        await images._singleflight("failed", fail)

    assert "failed" not in images._singleflight_tasks


@pytest.mark.asyncio
async def test_singleflight_shields_producer_from_cancelled_waiter():
    started = asyncio.Event()
    release = asyncio.Event()

    async def produce():
        started.set()
        await release.wait()
        return b"bmp", strong_etag(b"bmp")

    waiter = asyncio.create_task(images._singleflight("cancelled-waiter", produce))
    await started.wait()
    producer_task = images._singleflight_tasks["cancelled-waiter"]
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert not producer_task.cancelled()
    release.set()
    assert await producer_task == (b"bmp", strong_etag(b"bmp"))
    assert "cancelled-waiter" not in images._singleflight_tasks
