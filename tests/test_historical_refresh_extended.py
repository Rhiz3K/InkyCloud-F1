"""Extended completeness and persistence coverage for historical refreshes."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import historical_refresh as historical


def _response(payload: dict):
    return SimpleNamespace(json=lambda: payload)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_current_year_and_refresh_completion_contract():
    assert historical._current_year() == datetime.now(timezone.utc).year
    assert historical.HistoricalRefreshResult((), (), 1).completed is True
    assert historical.HistoricalRefreshResult((), (), 0).completed is False
    assert historical.HistoricalRefreshResult((), ("monza",), 1).completed is False


def test_result_formatters_reject_missing_required_values_and_time():
    entry = {
        "Driver": {"code": "DRV", "familyName": "Driver"},
        "Constructor": {"name": "Team"},
    }
    with pytest.raises(ValueError, match="result time"):
        historical._format_result(1, entry, time_value=None)
    with pytest.raises(ValueError, match="Driver.code"):
        historical._format_result(
            1,
            {**entry, "Driver": {"code": "", "familyName": "Driver"}},
            time_value="1:00",
        )


@pytest.mark.asyncio
async def test_fetch_status_handles_empty_qualifying_and_race_responses():
    empty = _response({})
    qualifying = _response({"MRData": {"RaceTable": {"Races": [{"QualifyingResults": []}]}}})
    fetch = AsyncMock(side_effect=[empty, empty, empty])
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza")
    assert outcome == historical.CircuitFetchOutcome(None, True)

    fetch = AsyncMock(side_effect=[qualifying, empty, qualifying, empty, qualifying, empty])
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza")
    assert outcome == historical.CircuitFetchOutcome(None, True)


@pytest.mark.asyncio
async def test_fetch_status_marks_incomplete_podium_and_http_failures():
    qualifying = _response(
        {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "QualifyingResults": [
                                {
                                    "position": "1",
                                    "Driver": {"code": "DRV", "familyName": "Driver"},
                                    "Constructor": {"name": "Team"},
                                    "Q3": "1:00",
                                }
                            ]
                        }
                    ]
                }
            }
        }
    )
    race = _response(
        {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "Results": [
                                {
                                    "position": "1",
                                    "Driver": {"code": "DRV", "familyName": "Driver"},
                                    "Constructor": {"name": "Team"},
                                    "Time": {"time": "1:30"},
                                }
                            ]
                        }
                    ]
                }
            }
        }
    )
    fetch = AsyncMock(side_effect=[qualifying, race, _status_error(404), _status_error(500)])
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza")
    assert outcome.results is None
    assert outcome.completed is False


@pytest.mark.parametrize(
    ("results", "existing", "expected"),
    [
        ({"season": 2025}, None, False),
        ({"season": None}, {"season": 2026}, False),
        ({"season": "bad"}, {"season": 2026}, False),
        ({"season": 2025}, {"season": 2026}, True),
        ({"season": 2026}, {"season": 2025}, False),
    ],
)
def test_would_regress_season_validates_types_and_order(results, existing, expected):
    assert historical._would_regress_season(results, existing) is expected


class _AsyncClientContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_main_handles_filter_updates_regressions_unchanged_and_failures(tmp_path):
    path = tmp_path / "circuits.json"
    circuits = {
        "regress": {"historical": {"season": 2026, "value": "new"}},
        "changed": {"historical": {"season": 2025, "value": "old"}},
        "same": {"historical": {"season": 2026, "value": "same"}},
        "missing": {},
    }
    path.write_text(json.dumps(circuits), encoding="utf-8")
    outcomes = [
        historical.CircuitFetchOutcome({"season": 2025, "value": "older"}, True),
        historical.CircuitFetchOutcome({"season": 2026, "value": "new"}, True),
        historical.CircuitFetchOutcome({"season": 2026, "value": "same"}, True),
        historical.CircuitFetchOutcome(None, False),
    ]
    write = MagicMock()
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch(
            "app.services.historical_refresh.httpx.AsyncClient", return_value=_AsyncClientContext()
        ),
        patch(
            "app.services.historical_refresh._fetch_results_with_status",
            new=AsyncMock(side_effect=outcomes),
        ),
        patch("app.services.historical_refresh.asyncio.sleep", new=AsyncMock()),
        patch("app.services.historical_refresh.atomic_write_json", write),
    ):
        result = await historical.main()

    assert result == historical.HistoricalRefreshResult(("changed",), ("missing",), 4)
    write.assert_called_once()

    with patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path):
        result = await historical.main("unknown")
    assert result == historical.HistoricalRefreshResult((), ("unknown",), 0)


@pytest.mark.asyncio
async def test_main_does_not_write_when_filtered_result_is_unchanged(tmp_path):
    path = tmp_path / "circuits.json"
    existing = {"season": 2026, "value": "same"}
    path.write_text(json.dumps({"monza": {"historical": existing}}), encoding="utf-8")
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch(
            "app.services.historical_refresh.httpx.AsyncClient", return_value=_AsyncClientContext()
        ),
        patch(
            "app.services.historical_refresh._fetch_results_with_status",
            new=AsyncMock(return_value=historical.CircuitFetchOutcome(existing, True)),
        ),
        patch("app.services.historical_refresh.asyncio.sleep", new=AsyncMock()),
        patch("app.services.historical_refresh.atomic_write_json") as write,
    ):
        result = await historical.main("monza")

    assert result == historical.HistoricalRefreshResult((), (), 1)
    write.assert_not_called()
