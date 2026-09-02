"""Edge-case coverage for JSON API route handlers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.models import PerfMetricsPayload, TeamEntry, TeamsData
from app.routes import api


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
            "path": "/api/test",
            "raw_path": b"/api/test",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        }
    )


@pytest.mark.parametrize(
    ("value", "round_num", "expected"),
    [
        (None, 1, False),
        ({"bad": "shape"}, 1, False),
        ("not-a-number", 1, False),
        ("2", 2, True),
        (2.0, 2, True),
    ],
)
def test_matches_round_validates_input(value, round_num, expected):
    assert api._matches_round({"round": value}, round_num) is expected


def test_operational_auth_covers_public_empty_header_bearer_and_rejection():
    with patch("app.routes.api.config.ADMIN_API_TOKEN", None):
        api._require_operational_api_auth(_request())

    with (
        patch("app.routes.api.config.ADMIN_API_TOKEN", SecretStr("")),
        pytest.raises(HTTPException) as error,
    ):
        api._require_operational_api_auth(_request())
    assert error.value.status_code == 503

    with patch("app.routes.api.config.ADMIN_API_TOKEN", SecretStr("secret")):
        api._require_operational_api_auth(_request({"X-Admin-Token": "secret"}))
        api._require_operational_api_auth(_request({"Authorization": "Bearer secret"}))
        with pytest.raises(HTTPException) as error:
            api._require_operational_api_auth(_request())
        assert error.value.status_code == 401
        with pytest.raises(HTTPException):
            api._require_operational_api_auth(_request({"X-Admin-Token": "wrong"}))


def test_operational_auth_rejects_non_ascii_token_with_401():
    # ``secrets.compare_digest`` raises TypeError for non-ASCII ``str``; that must not become a 500.
    with (
        patch("app.routes.api.config.ADMIN_API_TOKEN", SecretStr("secret")),
        pytest.raises(HTTPException) as error,
    ):
        api._require_operational_api_auth(_request({"X-Admin-Token": "s\xe9cret"}))
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda request: api.get_stats(request),
        lambda request: api.get_perf_metrics(request, hours=24),
        lambda request: api.get_stats_history(request, limit=1),
    ],
)
async def test_operational_reads_apply_rate_limit_before_authentication(call):
    limited = HTTPException(status_code=429, detail="Rate limit exceeded")
    with (
        patch("app.routes.api.config.ADMIN_API_TOKEN", SecretStr("secret")),
        patch("app.routes.api.enforce_rate_limit", side_effect=limited),
        pytest.raises(HTTPException) as error,
    ):
        await call(_request())
    assert error.value.status_code == 429


@pytest.mark.parametrize(
    ("page_path", "expected"),
    [
        ("/", "/"),
        ("/cs/", "/cs/"),
        ("/cs", "/cs/"),
        ("/configure/calendar", "/configure/calendar"),
        ("/pt-BR/configure/teams/", "/pt-BR/configure/teams"),
        ("/api/docs/html", "/api/docs/html"),
        ("/calendar.bmp", "/other"),
        ("/cs/unknown", "/other"),
        ("/xx/stats", "/other"),
    ],
)
def test_normalize_perf_page_path_collapses_unknown_pages(page_path, expected):
    assert api.normalize_perf_page_path(page_path) == expected


@pytest.mark.asyncio
async def test_post_perf_metrics_stores_normalized_page_path():
    db = SimpleNamespace(save_perf_metric=AsyncMock())
    payload = PerfMetricsPayload(page_path="/cs/whatever", lcp_ms=100)
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.get_database", return_value=db),
        patch("app.routes.api.create_supervised_task"),
        patch("app.routes.api.track_event", new=MagicMock()) as track_event,
    ):
        result = await api.post_perf_metrics(payload, _request({"User-Agent": "ua"}))

    assert result == {"status": "ok"}
    assert db.save_perf_metric.await_args.kwargs["page_path"] == api.PERF_METRIC_OTHER_PAGE
    assert track_event.call_args.kwargs["url"] == api.PERF_METRIC_OTHER_PAGE


@pytest.mark.asyncio
async def test_get_season_races_uses_remote_fallback():
    service = SimpleNamespace(
        get_all_races_from_static=lambda _year: [],
        get_season_races=AsyncMock(return_value=[{"round": "1"}]),
    )
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
    ):
        result = await api.get_season_races(2026, _request(), service)

    assert result == {"year": 2026, "races": [{"round": "1"}]}
    service.get_season_races.assert_awaited_once_with(2026)


@pytest.mark.asyncio
async def test_get_race_detail_covers_validation_static_remote_and_missing():
    request = _request()
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
    ):
        with pytest.raises(HTTPException) as error:
            await api.get_race_detail(2026, 31, request, SimpleNamespace())
        assert error.value.status_code == 422

        static = SimpleNamespace(
            get_all_races_from_static=lambda _year: [{"round": "2", "name": "Static"}],
            get_race_by_round=AsyncMock(),
        )
        assert (await api.get_race_detail(2026, 2, request, static))["name"] == "Static"
        static.get_race_by_round.assert_not_awaited()

        remote = SimpleNamespace(
            get_all_races_from_static=lambda _year: [{"round": "invalid"}],
            get_race_by_round=AsyncMock(return_value={"round": "3", "name": "Remote"}),
        )
        assert (await api.get_race_detail(2026, 3, request, remote))["name"] == "Remote"

        remote.get_race_by_round.return_value = None
        with pytest.raises(HTTPException) as error:
            await api.get_race_detail(2026, 4, request, remote)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_get_teams_serializes_data_and_maps_failures_to_503():
    data = TeamsData(season=2026, teams=[TeamEntry(constructor_name="Team")])
    service = SimpleNamespace(get_teams_and_drivers=AsyncMock(return_value=data))
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
        patch("app.routes.api.TeamsService", return_value=service),
    ):
        result = await api.get_teams(2026, _request())
    assert result["season"] == 2026
    assert result["teams"][0]["constructor_name"] == "Team"

    service.get_teams_and_drivers.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
        patch("app.routes.api.TeamsService", return_value=service),
        pytest.raises(HTTPException) as error,
    ):
        await api.get_teams(2026, _request())
    assert error.value.status_code == 503


def test_team_id_and_driver_number_helpers_delegate_and_handle_unknown_team():
    with patch("app.routes.api.get_driver_number", return_value=4) as number:
        assert api._get_driver_number("NOR", 2026) == 4
    number.assert_called_once_with("NOR", 2026)

    with patch.dict(api.TEAM_ID_MAP, {"Ferrari": "ferrari"}, clear=True):
        assert api._get_team_id("Scuderia Ferrari") == "ferrari"
        assert api._get_team_id("Unknown") is None


@pytest.mark.asyncio
async def test_standings_leader_returns_full_empty_and_failure_payloads():
    driver = SimpleNamespace(
        driver_name="Norris",
        driver_code="NOR",
        driver_given_name="Lando",
        constructor_name="McLaren",
    )
    constructor = SimpleNamespace(constructor_name="McLaren")
    service = SimpleNamespace(
        get_driver_standings=AsyncMock(return_value=[driver]),
        get_constructor_standings=AsyncMock(return_value=[constructor]),
    )
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
        patch("app.routes.api.get_current_f1_season", return_value=2026),
        patch("app.services.standings_service.StandingsService", return_value=service),
        patch("app.routes.api._get_driver_number", return_value=4),
        patch("app.routes.api._get_team_id", return_value="mclaren"),
    ):
        result = await api.get_standings_leader(_request())

    assert result == {
        "season": 2026,
        "leader_team": {"name": "McLaren", "id": "mclaren"},
        "leader_driver": {
            "name": "NORRIS",
            "code": "NOR",
            "full_name": "Lando Norris",
            "number": 4,
            "team": "McLaren",
        },
        "has_data": True,
    }

    service.get_driver_standings.return_value = []
    service.get_constructor_standings.return_value = []
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
        patch("app.services.standings_service.StandingsService", return_value=service),
    ):
        result = await api.get_standings_leader(_request(), 2026)
    assert result["has_data"] is False

    service.get_driver_standings.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.routes.api.is_supported_f1_season", return_value=True),
        patch("app.services.standings_service.StandingsService", return_value=service),
    ):
        result = await api.get_standings_leader(_request(), 2026)
    assert result == {
        "season": 2026,
        "leader_team": None,
        "leader_driver": None,
        "has_data": False,
    }
