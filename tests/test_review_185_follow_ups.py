"""Regression tests for the follow-up tier of the 2026-11-22 full review (issue #185)."""

from datetime import UTC, datetime

import pytest

from app.services import teams_extra as extras
from app.services.f1_service import F1Service
from app.services.teams_extra import TeamsExtra, build_teams_extra_context

# --- B6: a date-only race keeps its calendar date in the Teams panel ---------------------------


def _season_with_first_race(monkeypatch, *, time: str | None, race_date: str = "2026-03-14"):
    races = [race.model_copy(deep=True) for race in F1Service.get_season_from_static(2026)]
    races[0].date = race_date
    races[0].time = time
    monkeypatch.setattr(
        extras.F1Service,
        "get_season_from_static",
        staticmethod(lambda year: races if year == 2026 else []),
    )
    return races


@pytest.mark.parametrize("timezone_name", ["America/New_York", "Europe/Prague", "Asia/Tokyo"])
def test_date_only_race_keeps_its_date_and_has_no_invented_hour(monkeypatch, timezone_name):
    from app.services.renderer_teams_extras import _date_time

    _season_with_first_race(monkeypatch, time=None)
    now = datetime(2026, 3, 10, 12, tzinfo=UTC)

    ctx = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=now, timezone_name=timezone_name
    )

    assert ctx is not None and ctx.next_round == 1
    assert ctx.start_local.date().isoformat() == "2026-03-14"
    assert ctx.start_time_known is False
    assert ctx.rounds[0].date.isoformat() == "2026-03-14"
    assert _date_time(ctx, {}).endswith("14.03. · –")


def test_date_only_race_stays_upcoming_until_its_local_day_ends(monkeypatch):
    _season_with_first_race(monkeypatch, time=None)
    # 00:01Z on race day is still the previous evening in New York.
    early = datetime(2026, 3, 14, 0, 1, tzinfo=UTC)
    late = datetime(2026, 3, 15, 5, 0, tzinfo=UTC)  # 01:00 the next day in New York

    during = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=early, timezone_name="America/New_York"
    )
    after = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=late, timezone_name="America/New_York"
    )

    assert during is not None and during.next_round == 1 and during.days_left == 1
    assert after is not None and after.next_round == 2
    assert after.rounds[0].done is True


def test_explicit_offset_is_not_overwritten_with_utc(monkeypatch):
    _season_with_first_race(monkeypatch, time="14:00:00+02:00")
    now = datetime(2026, 3, 10, tzinfo=UTC)

    ctx = build_teams_extra_context(TeamsExtra.COUNTDOWN, 2026, now=now, timezone_name="UTC")

    assert ctx is not None and ctx.start_time_known is True
    assert ctx.start_local == datetime(2026, 3, 14, 12, tzinfo=UTC)


# --- C4: parameters that do not affect selection do not split the cache ------------------------


def test_cache_key_distinguishes_zero_from_missing():
    from app.routes.images import _get_cache_key

    auto = _get_cache_key("en", None, None, None, None, True, "race_day", "1bit")
    zero = _get_cache_key("en", 0, 0, None, None, True, "race_day", "1bit")
    assert auto == "en:next:next:auto:default:race_day:1bit"
    assert zero == "en:0:0:auto:default:race_day:1bit"


@pytest.mark.asyncio
async def test_round_without_year_uses_the_auto_selected_pregenerated_file():
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from app.routes import images
    from tests.test_routes_images import _request

    f1 = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value={"season": 2026, "round": 1}),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    lookup = MagicMock(return_value=None)
    calls = []

    async def rendered(key, producer):
        calls.append((key, producer.keywords))
        return (b"bmp", '"etag"')

    images.get_bmp_cache().clear()
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", lookup),
        patch("app.routes.images._singleflight", side_effect=rendered),
        patch("app.routes.images._record_calendar_api_call"),
    ):
        await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=5,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )

    assert lookup.call_args.kwargs["race_round"] is None
    key, keywords = calls[0]
    assert key.endswith("|en:next:next:auto:default:race_day:1bit")
    assert keywords["race_round"] is None
    images.get_bmp_cache().clear()


# --- C5: gzip and identity bodies never share a strong ETag; 304s vary on encoding -------------


def _etag_app():
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.responses import Response
    from starlette.routing import Route

    from app.main import EncodingAwareETagMiddleware
    from app.utils.etag import if_none_match_matches, strong_etag

    body = b"BM" + bytes(4000)
    etag = strong_etag(body)

    async def image(request):
        if if_none_match_matches(request.headers.get("If-None-Match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        return Response(body, media_type="image/bmp", headers={"ETag": etag})

    return Starlette(
        routes=[Route("/image.bmp", image)],
        middleware=[
            Middleware(EncodingAwareETagMiddleware),
            Middleware(GZipMiddleware, minimum_size=500),
        ],
    ), etag


def test_gzip_response_gets_weak_etag_and_vary():
    from fastapi.testclient import TestClient

    app, etag = _etag_app()
    client = TestClient(app)

    identity = client.get("/image.bmp", headers={"Accept-Encoding": "identity"})
    gzipped = client.get("/image.bmp", headers={"Accept-Encoding": "gzip"})

    assert identity.headers["etag"] == etag
    assert "content-encoding" not in identity.headers
    assert gzipped.headers["content-encoding"] == "gzip"
    assert gzipped.headers["etag"] == f"W/{etag}"
    for response in (identity, gzipped):
        assert response.headers["vary"].lower().count("accept-encoding") == 1


def test_revalidation_keeps_the_negotiated_validator():
    from fastapi.testclient import TestClient

    app, etag = _etag_app()
    client = TestClient(app)

    weak = client.get(
        "/image.bmp", headers={"Accept-Encoding": "gzip", "If-None-Match": f"W/{etag}"}
    )
    strong = client.get(
        "/image.bmp", headers={"Accept-Encoding": "identity", "If-None-Match": etag}
    )

    assert weak.status_code == strong.status_code == 304
    assert weak.headers["etag"] == f"W/{etag}"
    assert strong.headers["etag"] == etag
    assert "accept-encoding" in weak.headers["vary"].lower()
    assert "accept-encoding" in strong.headers["vary"].lower()


def test_encoding_aware_etag_wraps_gzip_in_the_app():
    from starlette.middleware.gzip import GZipMiddleware

    from app.main import EncodingAwareETagMiddleware, app

    order = [middleware.cls for middleware in app.user_middleware]
    assert order.index(EncodingAwareETagMiddleware) < order.index(GZipMiddleware)


@pytest.mark.asyncio
async def test_encoding_aware_etag_extends_existing_vary_and_skips_non_http():
    from app.main import EncodingAwareETagMiddleware

    sent = []

    async def downstream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"etag", b'"abc"'), (b"vary", b"Cookie")],
            }
        )

    async def collect(message):
        sent.append(message)

    middleware = EncodingAwareETagMiddleware(downstream)
    await middleware({"type": "http", "headers": []}, None, collect)
    headers = dict(sent[0]["headers"])
    assert headers[b"vary"] == b"Cookie, Accept-Encoding"
    assert headers[b"etag"] == b'"abc"'

    passthrough = []

    async def lifespan_app(scope, receive, send):
        passthrough.append(scope["type"])

    await EncodingAwareETagMiddleware(lifespan_app)({"type": "lifespan"}, None, collect)
    assert passthrough == ["lifespan"]
