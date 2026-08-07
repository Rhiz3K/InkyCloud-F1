"""Refresh bundled circuit history from Jolpica without accepting partial rows."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import config
from app.services.circuit_data import ensure_runtime_circuits_data
from app.services.http_client import get_shared_http_client
from app.utils.atomic_io import atomic_write_json
from app.utils.f1_season import get_current_f1_season
from app.utils.http import AsyncPacer, fetch_with_retry, is_transient_http_status
from app.utils.jolpica import get_jolpica_base_url, get_jolpica_pacer
from app.utils.material_diff import has_material_change
from app.utils.result_entries import ResultEntry, get_result_mapping, sort_entries_by_position

PODIUM_SIZE = 3
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitFetchOutcome:
    """Results and completion state for one circuit refresh attempt."""

    results: dict | None
    completed: bool
    transient_only: bool = False


@dataclass(frozen=True)
class HistoricalRefreshResult:
    """Aggregate changed, failed, and considered circuits from a refresh run."""

    updated_circuits: tuple[str, ...]
    failed_circuits: tuple[str, ...]
    considered_circuits: int
    transient_failed_circuits: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        """Return whether circuits were considered and none of the fetched ones failed."""
        return self.considered_circuits > 0 and not self.failed_circuits

    @property
    def can_advance_freshness(self) -> bool:
        """Return whether the run completed or failed only for transient upstream reasons."""
        transient_failures = set(self.transient_failed_circuits)
        return self.considered_circuits > 0 and all(
            circuit_id in transient_failures for circuit_id in self.failed_circuits
        )


def _current_year() -> int:
    """Return the current F1 season at execution time."""
    return get_current_f1_season()


def _needs_refresh(circuit: Mapping[str, Any], current_season: int) -> bool:
    """Return whether a circuit lacks immutable final results for the current season."""
    historical = circuit.get("historical")
    if not isinstance(historical, dict):
        return True
    try:
        return int(historical.get("season", 0)) < current_season
    except TypeError, ValueError:
        return True


def has_material_historical_change(results: dict, existing_historical: dict | None) -> bool:
    """Return whether historical results changed beyond the refresh timestamp."""
    return has_material_change(results, existing_historical, ignored_keys=("updated_at",))


def _required_text(mapping: Mapping[str, Any], key: str, *, context: str) -> str:
    """Read and normalize a required non-empty string from an upstream mapping."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {context}.{key}")
    return value.strip()


def _format_result(
    position: int,
    entry: ResultEntry,
    *,
    time_value: object,
) -> dict[str, object]:
    """Normalize one validated upstream result row for persistent storage."""
    driver = get_result_mapping(entry, "Driver")
    constructor = get_result_mapping(entry, "Constructor")
    if not isinstance(time_value, str) or not time_value.strip():
        raise ValueError(f"Missing result time for position {position}")

    return {
        "pos": position,
        "code": _required_text(driver, "code", context="Driver"),
        "name": _required_text(driver, "familyName", context="Driver"),
        "team": _required_text(constructor, "name", context="Constructor"),
        "time": time_value.strip(),
    }


def _format_qualifying_result(position: int, entry: ResultEntry) -> dict[str, object]:
    """Convert one complete qualifying row into stored historical JSON."""
    return _format_result(
        position,
        entry,
        time_value=entry.get("Q3") or entry.get("Q2") or entry.get("Q1"),
    )


def _format_race_result(position: int, entry: ResultEntry) -> dict[str, object]:
    """Convert one complete race row into stored historical JSON."""
    time_data = get_result_mapping(entry, "Time")
    return _format_result(position, entry, time_value=time_data.get("time"))


async def _fetch_results_with_status(
    client: httpx.AsyncClient,
    circuit_id: str,
    *,
    pacer: AsyncPacer | None = None,
) -> CircuitFetchOutcome:
    """Fetch a podium and retain whether every upstream attempt completed cleanly."""
    current_year = _current_year()
    had_valid_response = False
    had_failure = False
    api_base = get_jolpica_base_url()
    pacer = pacer or get_jolpica_pacer(api_base)
    for year in (current_year, current_year - 1, current_year - 2):
        try:
            qualifying_response = await fetch_with_retry(
                client,
                f"{api_base}/{year}/circuits/{circuit_id}/qualifying.json?limit=100",
                max_retries=config.JOLPICA_MAX_RETRIES,
                retry_base_delay=2.0,
                pacer=pacer,
                logger=logger,
            )
            had_valid_response = True
            qualifying_races = (
                qualifying_response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )
            if not qualifying_races:
                continue

            race_response = await fetch_with_retry(
                client,
                f"{api_base}/{year}/circuits/{circuit_id}/results.json?limit=100",
                max_retries=config.JOLPICA_MAX_RETRIES,
                retry_base_delay=2.0,
                pacer=pacer,
                logger=logger,
            )
            had_valid_response = True
            race_races = (
                race_response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            )
            if not race_races:
                continue

            qualifying = sort_entries_by_position(qualifying_races[0].get("QualifyingResults"))[
                :PODIUM_SIZE
            ]
            race = sort_entries_by_position(race_races[0].get("Results"))[:PODIUM_SIZE]
            if len(qualifying) != PODIUM_SIZE or len(race) != PODIUM_SIZE:
                raise ValueError("Incomplete podium results")

            return CircuitFetchOutcome(
                results={
                    "season": year,
                    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "qualifying": [
                        _format_qualifying_result(position, entry) for position, entry in qualifying
                    ],
                    "race": [_format_race_result(position, entry) for position, entry in race],
                },
                completed=not had_failure,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 404:
                had_valid_response = True
            elif is_transient_http_status(status_code):
                logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
                return CircuitFetchOutcome(
                    results=None,
                    completed=False,
                    transient_only=not had_failure,
                )
            else:
                had_failure = True
                logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
            continue
        except httpx.TransportError as exc:
            logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
            return CircuitFetchOutcome(
                results=None,
                completed=False,
                transient_only=not had_failure,
            )
        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
            had_failure = True
            logger.warning("Historical request failed for %s/%s: %s", circuit_id, year, exc)
            continue

    return CircuitFetchOutcome(results=None, completed=had_valid_response and not had_failure)


async def fetch_results(client: httpx.AsyncClient, circuit_id: str) -> dict | None:
    """Fetch the newest complete qualifying and race podium for a circuit."""
    return (await _fetch_results_with_status(client, circuit_id)).results


def _would_regress_season(results: dict, existing_historical: object) -> bool:
    """Prevent a transient current-year failure from replacing newer stored results."""
    if not isinstance(existing_historical, dict):
        return False
    result_season = results.get("season")
    existing_season = existing_historical.get("season")
    if not isinstance(result_season, (str, int)) or not isinstance(existing_season, (str, int)):
        return False
    try:
        return int(result_season) < int(existing_season)
    except TypeError, ValueError:
        return False


async def main(circuit_filter: str | None = None) -> HistoricalRefreshResult:
    """Update persistent historical data and report completion separately from changes."""
    circuits_path = ensure_runtime_circuits_data()
    with open(circuits_path, encoding="utf-8") as handle:
        circuits = json.load(handle)

    if circuit_filter:
        circuit_ids = [circuit_filter] if circuit_filter in circuits else []
        if not circuit_ids:
            logger.error("Circuit %s not found in circuits_data.json", circuit_filter)
            return HistoricalRefreshResult((), (circuit_filter,), 0)
    else:
        circuit_ids = list(circuits)

    logger.info("Updating historical results for %s circuits", len(circuit_ids))
    updated_circuits: list[str] = []
    failed_circuits: list[str] = []
    transient_failed_circuits: list[str] = []
    current_season = _current_year()
    refresh_ids = (
        list(circuit_ids)
        if circuit_filter
        else [
            circuit_id
            for circuit_id in circuit_ids
            if _needs_refresh(circuits[circuit_id], current_season)
        ]
    )
    skipped_count = len(circuit_ids) - len(refresh_ids)
    if skipped_count:
        logger.info(
            "Skipping %s circuits with final results for season %s",
            skipped_count,
            current_season,
        )

    client = get_shared_http_client(httpx.AsyncClient, timeout=30)
    pacer = get_jolpica_pacer()
    try:
        for circuit_id in refresh_ids:
            outcome = await _fetch_results_with_status(client, circuit_id, pacer=pacer)
            results = outcome.results
            if not outcome.completed:
                failed_circuits.append(circuit_id)
                if outcome.transient_only:
                    transient_failed_circuits.append(circuit_id)
            if results:
                existing_historical = circuits[circuit_id].get("historical")
                if _would_regress_season(results, existing_historical):
                    logger.info(
                        "Kept newer stored season %s for %s",
                        existing_historical.get("season"),
                        circuit_id,
                    )
                elif has_material_historical_change(results, existing_historical):
                    circuits[circuit_id]["historical"] = results
                    updated_circuits.append(circuit_id)
                    logger.info(
                        "Updated %s historical results from %s",
                        circuit_id,
                        results["season"],
                    )
                else:
                    logger.debug("Historical results unchanged for %s", circuit_id)
            else:
                logger.info("No complete historical data for %s", circuit_id)
    except asyncio.CancelledError:
        if updated_circuits:
            try:
                atomic_write_json(circuits_path, circuits)
            except Exception:
                logger.exception("Could not persist partial historical results after cancellation")
            else:
                logger.info(
                    "Saved partial historical results to %s before cancellation (%s updated)",
                    circuits_path,
                    len(updated_circuits),
                )
        raise

    if updated_circuits:
        atomic_write_json(circuits_path, circuits)
        logger.info("Saved historical results to %s", circuits_path)

    logger.info(
        "Historical refresh updated %s/%s attempted circuits (%s skipped); %s failed",
        len(updated_circuits),
        len(refresh_ids),
        skipped_count,
        len(failed_circuits),
    )
    if failed_circuits:
        logger.info("Historical refresh failed for: %s", ", ".join(failed_circuits))
    return HistoricalRefreshResult(
        updated_circuits=tuple(updated_circuits),
        failed_circuits=tuple(failed_circuits),
        considered_circuits=len(circuit_ids),
        transient_failed_circuits=tuple(transient_failed_circuits),
    )
