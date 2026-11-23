"""Regression tests for the follow-up tier of the 2026-11-22 full review (issue #185)."""

from datetime import UTC, datetime

import pytest

from app.services import bmp_artifacts
from app.services import teams_extra as extras
from app.services.f1_service import F1Service
from app.services.teams_extra import TeamsExtra, build_teams_extra_context
from app.state import get_bmp_cache

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
    from app.routes.params import calendar_cache_key

    auto = calendar_cache_key("en", None, None, None, None, "race_day", "1bit")
    zero = calendar_cache_key("en", 0, 0, None, None, "race_day", "1bit")
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

    get_bmp_cache().clear()
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", lookup),
        patch("app.services.bmp_artifacts.singleflight", side_effect=rendered),
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
    assert keywords["race_data"] == {"season": 2026, "round": 1}
    get_bmp_cache().clear()


# --- C5: gzip and identity bodies never share a strong ETag; 304s vary on encoding -------------


def _etag_app(size=4002):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.responses import Response
    from starlette.routing import Route

    from app.main import EncodingAwareETagMiddleware
    from app.utils.etag import if_none_match_matches, strong_etag

    body = b"BM" + bytes(size - 2)
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


@pytest.mark.parametrize("size", [499, 500])
def test_gzip_response_gets_weak_etag_and_vary(size):
    from fastapi.testclient import TestClient

    app, etag = _etag_app(size)
    client = TestClient(app)

    identity = client.get("/image.bmp", headers={"Accept-Encoding": "identity"})
    gzipped = client.get("/image.bmp", headers={"Accept-Encoding": "gzip"})

    assert identity.headers["etag"] == etag
    assert "content-encoding" not in identity.headers
    assert gzipped.headers.get("content-encoding") == ("gzip" if size >= 500 else None)
    assert gzipped.headers["etag"] == f"W/{etag}"
    for encoding, response in (("identity", identity), ("gzip", gzipped)):
        revalidated = client.get(
            "/image.bmp",
            headers={"Accept-Encoding": encoding, "If-None-Match": response.headers["etag"]},
        )
        assert revalidated.status_code == 304
        assert revalidated.headers["etag"] == response.headers["etag"]
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


# --- C6: a render that straddles a cache clear does not repopulate stale bytes -----------------


@pytest.mark.asyncio
async def test_calendar_render_finishing_after_clear_is_served_but_not_cached(monkeypatch):
    import asyncio

    from app.state import bmp_cache_generation, clear_bmp_cache

    started, release = asyncio.Event(), asyncio.Event()

    async def slow_render(**_kwargs):
        started.set()
        await release.wait()
        return b"old-bytes", {}, True

    monkeypatch.setattr(bmp_artifacts, "render_calendar", slow_render)
    clear_bmp_cache()
    before = bmp_cache_generation()
    task = asyncio.create_task(
        bmp_artifacts.render_calendar_artifact(
            cache_key="c6",
            race_data=None,
            lang="en",
            target_tz="UTC",
            weather_type="off",
            display="1bit",
        )
    )
    await started.wait()
    clear_bmp_cache()  # scheduler refresh lands while the old render is in flight
    release.set()
    artifact = await task

    assert bmp_cache_generation() == before + 1
    assert artifact[0] == b"old-bytes"
    assert "c6" not in get_bmp_cache()

    release.clear()
    started.clear()
    task = asyncio.create_task(
        bmp_artifacts.render_calendar_artifact(
            cache_key="c6",
            race_data=None,
            lang="en",
            target_tz="UTC",
            weather_type="off",
            display="1bit",
        )
    )
    await started.wait()
    release.set()
    await task
    assert get_bmp_cache()["c6"][0] == b"old-bytes"
    clear_bmp_cache()


# --- C9: a failed extra panel preview is not cached under the extra URL ------------------------


@pytest.mark.asyncio
async def test_failed_extra_preview_fallback_is_marked_no_store(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from starlette.responses import Response

    from app.routes import previews
    from tests.test_routes_images import _request

    plain = Response(b"png", media_type="image/png", headers={"Cache-Control": "no-cache"})
    monkeypatch.setattr(previews, "enforce_rate_limit", MagicMock())
    monkeypatch.setattr(previews, "build_teams_extra_context", MagicMock(return_value=object()))
    monkeypatch.setattr(
        previews, "_render_teams_extra_preview", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(previews, "_cached_preview_response", AsyncMock(return_value=None))
    monkeypatch.setattr(previews, "_render_teams_preview", AsyncMock(return_value=plain))

    response = await previews.get_configure_preview_png(
        "teams",
        _request(),
        lang="en",
        tz=None,
        weather_type="off",
        extra=TeamsExtra.COUNTDOWN,
        display="1bit",
    )

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-preview-fallback"] == "plain"


@pytest.mark.asyncio
async def test_failed_extra_preview_marks_pregenerated_fallback(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from starlette.responses import Response

    from app.routes import previews
    from tests.test_routes_images import _request

    cached = Response(b"png", media_type="image/png", headers={"Cache-Control": "public"})
    monkeypatch.setattr(previews, "enforce_rate_limit", MagicMock())
    monkeypatch.setattr(previews, "build_teams_extra_context", MagicMock(return_value=object()))
    monkeypatch.setattr(
        previews, "_render_teams_extra_preview", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(previews, "_cached_preview_response", AsyncMock(return_value=cached))

    response = await previews.get_configure_preview_png(
        "teams",
        _request(),
        lang="en",
        tz=None,
        weather_type="off",
        extra=TeamsExtra.COUNTDOWN,
        display="1bit",
    )

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-preview-fallback"] == "plain"


# --- C11: S3 cleanup counts confirmed deletes; CLI shows the effective backup state -----------


def test_backup_cleanup_counts_only_confirmed_deletes(caplog):
    from datetime import timedelta
    from unittest.mock import MagicMock, patch

    from app.services.backup import cleanup_old_backups

    old = datetime.now(UTC) - timedelta(days=90)
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": f"f1_backup_{i}.db", "LastModified": old} for i in range(3)]}
    ]
    client.delete_objects.return_value = {
        "Deleted": [{"Key": "f1_backup_0.db"}],
        "Errors": [
            {"Key": "f1_backup_1.db", "Code": "AccessDenied"},
            {"Key": "f1_backup_2.db", "Message": "slow down"},
        ],
    }
    with patch("app.services.backup.config") as config, caplog.at_level("WARNING"):
        config.MINIMAL_DATA_MODE = False
        config.BACKUP_RETENTION_DAYS = 30
        config.S3_BUCKET_NAME = "bucket"
        assert cleanup_old_backups(client) == 1

    assert "AccessDenied" in caplog.text and "slow down" in caplog.text

    client.delete_objects.return_value = {"Errors": [{"Key": "f1_backup_1.db"}]}
    with patch("app.services.backup.config") as config:
        config.MINIMAL_DATA_MODE = False
        config.BACKUP_RETENTION_DAYS = 30
        config.S3_BUCKET_NAME = "bucket"
        assert cleanup_old_backups(client) == 0
        client.delete_objects.return_value = {}
        assert cleanup_old_backups(client) == 0


def test_backup_info_reports_minimal_mode_as_disabled(capsys):
    from unittest.mock import patch

    from scripts import backup_cli

    with patch("app.services.backup.config") as config:
        config.MINIMAL_DATA_MODE = True
        config.BACKUP_ENABLED = True
        config.S3_ENDPOINT_URL = "https://s3.example"
        config.S3_BUCKET_NAME = "bucket"
        config.S3_REGION = "auto"
        config.BACKUP_CRON = "0 3 * * *"
        config.BACKUP_RETENTION_DAYS = 7
        config.S3_ACCESS_KEY_ID = "a"
        config.S3_SECRET_ACCESS_KEY = "b"
        backup_cli.cmd_info()

    output = capsys.readouterr().out
    assert "Enabled:      False" in output
    assert "MINIMAL_DATA_MODE=true" in output


# --- E5: every key referenced by a template exists in every locale -----------------------------


def test_template_translation_keys_exist_in_every_locale():
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    keys: set[str] = set()
    for template in (root / "app" / "templates").rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        keys |= set(re.findall(r't\.get\("([a-z0-9_]+)"', source))
        keys |= set(re.findall(r"\bt\.([a-z][a-z0-9_]+)\b", source)) - {"get", "items", "keys"}

    for locale in sorted((root / "translations").glob("*.json")):
        translations = json.loads(locale.read_text(encoding="utf-8"))
        missing = sorted(keys - translations.keys())
        assert not missing, f"{locale.name}: {missing}"


# --- D8: the OpenAPI schema builds without duplicate operation IDs -----------------------------


def test_openapi_schema_has_no_duplicate_operation_ids():
    import warnings

    from app.main import app

    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()
    duplicates = [str(w.message) for w in caught if "Duplicate Operation ID" in str(w.message)]
    assert duplicates == []
    assert "/robots.txt" not in schema["paths"] and "/sitemap.xml" not in schema["paths"]
    assert "/calendar.bmp" in schema["paths"]


# --- B8: standings default to the same season as the leader API ------------------------------


@pytest.mark.asyncio
async def test_standings_default_to_the_current_f1_season(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import standings_service

    service = standings_service.StandingsService()
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(standings_service, "get_current_f1_season", lambda: 2026)
    monkeypatch.setattr(service, "_fetch_driver_standings", fetch)
    service._cache.clear()
    service._negative_cache.clear()

    await service.get_driver_standings()

    assert fetch.await_args.args[0] == 2026
    service._negative_cache.clear()


# --- C12: an empty tz query means "not set" on every image route -------------------------------


def test_empty_tz_is_treated_as_unset(monkeypatch):
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from app.routes import deps

    service = MagicMock()
    monkeypatch.setattr(deps, "F1Service", service)

    deps.get_f1_service(tz="")
    service.assert_called_once_with(timezone_name=None)
    with pytest.raises(HTTPException):
        deps.get_f1_service(tz="Not/AZone")


# --- Gemini A: round=0 / year=0 / race_key= are explicit, invalid selections ------------------


@pytest.mark.parametrize(
    "query",
    ["year=2026&round=0", "year=0&round=1", "year=2026&race_key="],
)
def test_falsy_explicit_selection_serves_uncached_error_not_next_race(monkeypatch, query):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routes import images as images_routes
    from app.state import clear_bmp_cache, get_bmp_cache

    monkeypatch.setattr(images_routes.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(images_routes, "_get_pregenerated_calendar_path", lambda **_kw: None)
    client = TestClient(app)
    clear_bmp_cache()
    next_race = client.get("/calendar.bmp?weather=false")
    clear_bmp_cache()

    response = client.get(f"/calendar.bmp?{query}&weather=false")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["etag"] != next_race.headers["etag"]
    assert get_bmp_cache() == {}


def test_falsy_explicit_selection_is_not_resolved_as_next_race_in_stats():
    from unittest.mock import MagicMock

    from app.routes.images import _get_race_info_for_stats

    service = MagicMock()
    service.get_all_races_from_static.side_effect = lambda year: (
        [{"round": "1", "race_key": "first", "race_name": "First"}] if year == 2026 else []
    )

    assert _get_race_info_for_stats(service, 2026, 0, None) == (False, 2026, 0, None)
    assert _get_race_info_for_stats(service, 2026, None, "") == (False, 2026, None, None)
    assert _get_race_info_for_stats(service, 0, 1, None) == (False, 0, 1, None)
    service.get_next_race_from_static.assert_not_called()
