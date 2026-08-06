"""Extended completeness and persistence coverage for historical refreshes."""

import asyncio
import json
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
    with patch("app.services.historical_refresh.get_current_f1_season", return_value=2025):
        assert historical._current_year() == 2025
    assert historical.HistoricalRefreshResult((), (), 1).completed is True
    assert historical.HistoricalRefreshResult((), (), 0).completed is False
    assert historical.HistoricalRefreshResult((), ("monza",), 1).completed is False
    assert historical.HistoricalRefreshResult((), (), 1).can_advance_freshness is True
    assert historical.HistoricalRefreshResult((), ("monza",), 1).can_advance_freshness is False
    assert (
        historical.HistoricalRefreshResult(
            (), ("monza",), 1, transient_failed_circuits=("monza",)
        ).can_advance_freshness
        is True
    )


@pytest.mark.parametrize(
    ("circuit", "current_season", "expected"),
    [
        ({}, 2026, True),
        ({"historical": None}, 2026, True),
        ({"historical": {"season": "bad"}}, 2026, True),
        ({"historical": {"season": 2025}}, 2026, True),
        ({"historical": {"season": "2026"}}, 2026, False),
        ({"historical": {"season": 2027}}, 2026, False),
    ],
)
def test_needs_refresh_skips_current_or_newer_final_results(circuit, current_season, expected):
    assert historical._needs_refresh(circuit, current_season) is expected


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
    fetch = AsyncMock(side_effect=[qualifying, race, _status_error(404), _status_error(400)])
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza")
    assert outcome.results is None
    assert outcome.completed is False
    assert outcome.transient_only is False


@pytest.mark.asyncio
async def test_fetch_status_stops_fallbacks_after_exhausted_rate_limit():
    fetch = AsyncMock(side_effect=_status_error(429))
    pacer = MagicMock()
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
        patch("app.services.historical_refresh.config.JOLPICA_MAX_RETRIES", 6),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza", pacer=pacer)

    assert outcome == historical.CircuitFetchOutcome(None, False, transient_only=True)
    fetch.assert_awaited_once()
    assert fetch.await_args.kwargs == {
        "max_retries": 6,
        "retry_base_delay": 2.0,
        "pacer": pacer,
        "logger": historical.logger,
    }


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


@pytest.mark.asyncio
async def test_main_handles_filter_updates_regressions_unchanged_and_failures(tmp_path):
    path = tmp_path / "circuits.json"
    circuits = {
        "regress": {"historical": {"season": 2026, "value": "new"}},
        "changed": {"historical": {"season": 2025, "value": "old"}},
        "same": {"historical": {"season": 2026, "value": "same"}},
        "missing": {},
        "permanent": {},
    }
    path.write_text(json.dumps(circuits), encoding="utf-8")
    outcomes = [
        historical.CircuitFetchOutcome({"season": 2025, "value": "older"}, True),
        historical.CircuitFetchOutcome({"season": 2026, "value": "new"}, True),
        historical.CircuitFetchOutcome({"season": 2026, "value": "same"}, True),
        historical.CircuitFetchOutcome(None, False, transient_only=True),
        historical.CircuitFetchOutcome(None, False),
    ]
    write = MagicMock()
    fetch = AsyncMock(side_effect=outcomes)
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2027),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=fetch),
        patch("app.services.historical_refresh.atomic_write_json", write),
    ):
        result = await historical.main()

    assert result == historical.HistoricalRefreshResult(
        ("changed",),
        ("missing", "permanent"),
        5,
        transient_failed_circuits=("missing",),
    )
    write.assert_called_once()
    pacers = [call.kwargs["pacer"] for call in fetch.await_args_list]
    assert len(pacers) == len(outcomes)
    assert all(isinstance(pacer, historical.AsyncPacer) for pacer in pacers)
    assert len({id(pacer) for pacer in pacers}) == 1, "every circuit must share one pacer"

    with patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path):
        result = await historical.main("unknown")
    assert result == historical.HistoricalRefreshResult((), ("unknown",), 0)


@pytest.mark.asyncio
async def test_main_does_not_write_when_filtered_result_is_unchanged(tmp_path):
    path = tmp_path / "circuits.json"
    existing = {"season": 2026, "value": "same"}
    path.write_text(json.dumps({"monza": {"historical": existing}}), encoding="utf-8")
    fetch = AsyncMock(return_value=historical.CircuitFetchOutcome(existing, True))
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=fetch),
        patch("app.services.historical_refresh.atomic_write_json") as write,
    ):
        result = await historical.main("monza")

    assert result == historical.HistoricalRefreshResult((), (), 1)
    fetch.assert_awaited_once()
    write.assert_not_called()


@pytest.mark.asyncio
async def test_main_persists_completed_circuits_before_cancellation(tmp_path):
    path = tmp_path / "circuits.json"
    circuits = {"monza": {}, "imola": {}}
    path.write_text(json.dumps(circuits), encoding="utf-8")

    async def fetch(_client, circuit_id, **_kwargs):
        if circuit_id == "imola":
            raise asyncio.CancelledError
        return historical.CircuitFetchOutcome({"season": 2026, "value": "complete"}, True)

    write = MagicMock()
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=fetch),
        patch("app.services.historical_refresh.atomic_write_json", write),
        pytest.raises(asyncio.CancelledError),
    ):
        await historical.main()

    persisted = write.call_args.args[1]
    assert persisted["monza"]["historical"] == {"season": 2026, "value": "complete"}
    assert "historical" not in persisted["imola"]


@pytest.mark.asyncio
async def test_main_skips_persistence_when_cancelled_before_any_updates(tmp_path):
    path = tmp_path / "circuits.json"
    path.write_text(json.dumps({"monza": {}}), encoding="utf-8")

    async def cancel(*_args, **_kwargs):
        raise asyncio.CancelledError

    write = MagicMock()
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=cancel),
        patch("app.services.historical_refresh.atomic_write_json", write),
        pytest.raises(asyncio.CancelledError),
    ):
        await historical.main()

    write.assert_not_called()


@pytest.mark.asyncio
async def test_main_preserves_cancellation_when_partial_persistence_fails(tmp_path, caplog):
    path = tmp_path / "circuits.json"
    path.write_text(json.dumps({"monza": {}, "imola": {}}), encoding="utf-8")

    async def fetch(_client, circuit_id, **_kwargs):
        if circuit_id == "imola":
            raise asyncio.CancelledError
        return historical.CircuitFetchOutcome({"season": 2026, "value": "complete"}, True)

    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=fetch),
        patch(
            "app.services.historical_refresh.atomic_write_json",
            side_effect=OSError("disk full"),
        ),
        caplog.at_level("ERROR", logger="app.services.historical_refresh"),
        pytest.raises(asyncio.CancelledError),
    ):
        await historical.main()

    assert "Could not persist partial historical results after cancellation" in caplog.text


@pytest.mark.asyncio
async def test_main_skips_current_season_circuits_but_never_a_named_one(tmp_path):
    path = tmp_path / "circuits.json"
    current = {"season": 2026, "value": "final"}
    path.write_text(
        json.dumps({"monza": {"historical": current}, "imola": {"historical": current}}),
        encoding="utf-8",
    )
    fetch = AsyncMock(return_value=historical.CircuitFetchOutcome(current, True))
    with (
        patch("app.services.historical_refresh.ensure_runtime_circuits_data", return_value=path),
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.get_shared_http_client", return_value=object()),
        patch("app.services.historical_refresh._fetch_results_with_status", new=fetch),
        patch("app.services.historical_refresh.atomic_write_json"),
    ):
        skipped = await historical.main()
        fetch.assert_not_awaited()

        forced = await historical.main("monza")

    assert skipped == historical.HistoricalRefreshResult((), (), 2)
    assert skipped.completed is True
    assert forced == historical.HistoricalRefreshResult((), (), 1)
    assert [call.args[1] for call in fetch.await_args_list] == ["monza"]
