"""Edge-case coverage for JSON API route handlers."""

import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.requests import Request

from app.models import PerfMetricsPayload, TeamEntry, TeamsData
from app.routes import api, images, previews, tracks
from app.services import f1_service as f1


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


@pytest.mark.asyncio
async def test_api_documentation_covers_public_routes_and_query_contracts():
    """Catch undocumented endpoints, query additions, changed defaults and enum choices."""
    application = FastAPI()
    for router in (api.router, images.router, previews.router, tracks.router):
        application.include_router(router)
    schema = application.openapi()
    documentation = (await api.api_info())["endpoints"]

    for path, operations in schema["paths"].items():
        if not (
            path == "/api"
            or path.startswith("/api/")
            or path.endswith(".bmp")
            or path.startswith("/preview/")
            and path.endswith(".png")
        ):
            continue
        documented_path = path.replace("{image_format}", "{svg|png|bmp}")
        assert documented_path in documentation, path
        endpoint = documentation[documented_path]
        for method, operation in operations.items():
            assert method.upper() in endpoint.get("methods", [endpoint.get("method")]), path
            if method != "get":
                continue
            parameters = {
                parameter["name"]: parameter["schema"]
                for parameter in operation.get("parameters", [])
                if parameter["in"] == "query"
            }
            documented_parameters = {
                name: parameter
                for name, parameter in endpoint.get("parameters", {}).items()
                if parameter.get("in", "query") == "query"
            }
            assert documented_parameters.keys() == parameters.keys(), path
            for name, parameter in parameters.items():
                documented = documented_parameters[name]
                for field in ("default", "minimum", "maximum"):
                    if field in parameter:
                        assert documented.get(field) == parameter[field], (path, name, field)
                if "$ref" in parameter:
                    parameter = schema["components"]["schemas"][parameter["$ref"].split("/")[-1]]
                if "enum" in parameter:
                    assert documented["values"] == parameter["enum"], (path, name)


@pytest.mark.asyncio
async def test_api_documentation_reports_operator_configuration(monkeypatch):
    """Documentation reflects configured defaults and auth without exposing token contents."""
    monkeypatch.setattr(api.config, "DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setattr(api.config, "ADMIN_API_TOKEN", SecretStr("private-token"))
    monkeypatch.setattr(api.config, "MINIMAL_DATA_MODE", True)
    monkeypatch.setattr(api.config, "IMAGE_RATE_LIMIT_PER_MINUTE", 15)
    documentation = await api.api_info()

    assert documentation["endpoints"]["/calendar.bmp"]["parameters"]["tz"]["default"] == "UTC"
    assert documentation["statistics"]["read_authentication"]["required"] is True
    assert documentation["statistics"]["enabled"] is False
    assert documentation["statistics"]["performance_sample_rate"] == 0
    assert documentation["rate_limits"]["image_requests"] == 15
    assert "private-token" not in str(documentation)


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
        api.get_stats,
        functools.partial(api.get_perf_metrics, hours=24),
        functools.partial(api.get_stats_history, limit=1),
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
async def test_post_perf_metrics_honors_opt_out(monkeypatch):
    monkeypatch.setattr(api.config, "MINIMAL_DATA_MODE", True)
    payload = PerfMetricsPayload(page_path="/cs/whatever", lcp_ms=100)
    with pytest.raises(HTTPException) as error:
        await api.post_perf_metrics(payload, _request({"User-Agent": "ua"}))
    assert error.value.status_code == 410


@pytest.mark.asyncio
async def test_get_season_races_uses_remote_fallback():
    service = SimpleNamespace(
        get_all_races_from_static=lambda _year: [],
        get_season_races=AsyncMock(return_value=[{"round": "1"}]),
    )
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
    ):
        result = await api.get_season_races(2026, _request(), service)

    assert result == {"year": 2026, "races": [{"round": "1"}]}
    service.get_season_races.assert_awaited_once_with(2026)


@pytest.mark.asyncio
async def test_get_race_detail_covers_validation_static_remote_and_missing():
    request = _request()
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
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
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
        patch("app.routes.api.TeamsService", return_value=service),
    ):
        result = await api.get_teams(2026, _request())
    assert result["season"] == 2026
    assert result["teams"][0]["constructor_name"] == "Team"

    service.get_teams_and_drivers.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
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
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
        patch("app.routes.api.get_current_f1_season", return_value=2026),
        patch("app.services.standings_service.StandingsService", return_value=service),
        patch("app.routes.api._get_driver_number", return_value=4),
        patch("app.routes.api._get_team_id", return_value="mclaren"),
    ):
        result = await api.get_standings_leader(_request())

    assert result == {
        "season": 2026,
        "leader_team": {"name": "McLaren", "id": "mclaren", "color": "#ff8000"},
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
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
        patch("app.services.standings_service.StandingsService", return_value=service),
    ):
        result = await api.get_standings_leader(_request(), 2026)
    assert result["has_data"] is False

    service.get_driver_standings.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.api.enforce_rate_limit"),
        patch("app.utils.f1_season.is_supported_f1_season", return_value=True),
        patch("app.services.standings_service.StandingsService", return_value=service),
    ):
        result = await api.get_standings_leader(_request(), 2026)
    assert result == {
        "season": 2026,
        "leader_team": None,
        "leader_driver": None,
        "has_data": False,
    }


@pytest.mark.asyncio
async def test_historical_race_detail_without_session_times(sepang_2017_payload):
    service = f1.F1Service(timezone="UTC")
    application = FastAPI()
    application.include_router(api.router)
    application.dependency_overrides[api.get_f1_service] = lambda: service
    response = SimpleNamespace(
        json=lambda: {"MRData": {"RaceTable": {"Races": [sepang_2017_payload]}}}
    )
    f1._reset_remote_caches_for_tests()
    try:
        with (
            patch.object(service, "get_all_races_from_static", return_value=[]),
            patch.object(f1, "get_shared_http_client", return_value=object()),
            patch.object(f1, "fetch_with_retry", new=AsyncMock(return_value=response)),
            patch("app.routes.api.enforce_rate_limit"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=application), base_url="http://test"
            ) as client:
                result = await client.get("/api/race/2017/15")
        assert result.status_code == 200
        assert len(result.json()["schedule"]) == 5
    finally:
        f1._reset_remote_caches_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "year,round_num,detail",
    [
        (1949, 1, "Unsupported F1 season"),
        (9999, 1, "Unsupported F1 season"),
        (1949, 0, "Unsupported F1 season"),
        (2017, 0, "Invalid race round"),
        (2017, 31, "Invalid race round"),
    ],
)
async def test_race_detail_rejects_invalid_selection_before_lookup(year, round_num, detail):
    service = SimpleNamespace(get_all_races_from_static=MagicMock(), get_race_by_round=AsyncMock())
    with patch("app.routes.api.enforce_rate_limit"), pytest.raises(HTTPException) as error:
        await api.get_race_detail(year, round_num, _request(), service)
    assert error.value.status_code == 422
    assert error.value.detail == detail
    service.get_all_races_from_static.assert_not_called()
    service.get_race_by_round.assert_not_awaited()
