"""Scheduled weather-cache maintenance jobs."""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timezone

from app.config import config
from app.services.database import get_database
from app.services.f1_service import F1Service
from app.services.weather_service import (
    WeatherData,
    WeatherService,
    load_circuit_weather_to_cache,
    load_prefetched_weather_from_db,
    prefetch_weather_for_next_race,
    set_cached_circuit_weather,
)
from app.utils.async_locks import LoopLockRegistry, get_loop_lock

logger = logging.getLogger(__name__)

_weather_fetch_locks: LoopLockRegistry = weakref.WeakKeyDictionary()


async def _fetch_all_circuits_weather_unlocked() -> None:
    """
    Fetch weather for all F1 circuits, cache in memory, and persist to DB.

    Iterates circuits from current season, fetches weather sequentially with
    1s pause, stores in cache and SQLite. Retries failed circuits up to 3x.
    Returns immediately if weather is disabled.
    """
    if not config.WEATHER_ENABLED:
        logger.debug("Weather is disabled, skipping fetch")
        return

    logger.info("Starting circuit weather fetch")

    try:
        db = get_database()
        f1_service = F1Service()
        weather_service = WeatherService(
            timeout=config.REQUEST_TIMEOUT,
            cache_minutes=config.WEATHER_CACHE_MINUTES,
        )

        # Get current F1 season
        current_year = datetime.now(timezone.utc).year

        # Get all races from static data
        all_races = f1_service.get_all_races_from_static(current_year)

        if not all_races:
            # Try next year (late in season, next year data might be available)
            all_races = f1_service.get_all_races_from_static(current_year + 1)

        if not all_races:
            logger.warning("No races found in static data for weather fetch")
            return

        # Extract unique circuits with coordinates
        seen_circuits: set[str] = set()
        circuits_to_fetch: list[dict] = []

        for race in all_races:
            circuit = race.get("circuit", {})
            circuit_id = circuit.get("circuitId")

            if not circuit_id or circuit_id in seen_circuits:
                continue

            lat_str = circuit.get("lat")
            lon_str = circuit.get("long")

            if not lat_str or not lon_str:
                logger.debug("Circuit %s missing coordinates, skipping", circuit_id)
                continue

            seen_circuits.add(circuit_id)
            circuits_to_fetch.append(
                {
                    "id": circuit_id,
                    "name": circuit.get("name", circuit_id),
                    "lat": float(lat_str),
                    "lon": float(lon_str),
                }
            )

        logger.info("Fetching weather for %d circuits", len(circuits_to_fetch))

        # Track failed circuits for retry
        failed: list[dict] = []
        success_count = 0
        max_attempts = 3

        # Round 1: Fetch all circuits
        for circuit in circuits_to_fetch:
            weather = await _fetch_single_circuit_weather(
                weather_service, circuit["lat"], circuit["lon"]
            )

            if weather:
                # Save to both in-memory cache and SQLite
                set_cached_circuit_weather(circuit["id"], weather)
                await db.save_circuit_weather(
                    circuit_id=circuit["id"],
                    circuit_name=circuit["name"],
                    temperature_c=weather.temperature_c,
                    weather_code=weather.weather_code,
                    precipitation_probability=weather.precipitation_probability,
                )
                success_count += 1
                logger.debug("Weather fetched for %s: %s", circuit["id"], weather.temp_display)
            else:
                circuit["attempts"] = 1
                failed.append(circuit)

            # 1 second delay between requests
            await asyncio.sleep(1)

        # Retry rounds (attempts 2-3)
        for round_num in range(2, max_attempts + 1):
            if not failed:
                break

            logger.debug("Weather retry round %d, %d circuits remaining", round_num, len(failed))
            still_failed: list[dict] = []

            for circuit in failed:
                weather = await _fetch_single_circuit_weather(
                    weather_service, circuit["lat"], circuit["lon"]
                )

                if weather:
                    set_cached_circuit_weather(circuit["id"], weather)
                    await db.save_circuit_weather(
                        circuit_id=circuit["id"],
                        circuit_name=circuit["name"],
                        temperature_c=weather.temperature_c,
                        weather_code=weather.weather_code,
                        precipitation_probability=weather.precipitation_probability,
                    )
                    success_count += 1
                    logger.debug(
                        "Weather fetched for %s on attempt %d",
                        circuit["id"],
                        round_num,
                    )
                else:
                    circuit["attempts"] = round_num
                    still_failed.append(circuit)

                await asyncio.sleep(1)

            failed = still_failed

        # Log final results
        if failed:
            failed_ids = [c["id"] for c in failed]
            logger.warning(
                "Weather fetch failed for %d circuits after %d attempts: %s",
                len(failed),
                max_attempts,
                failed_ids,
            )

        logger.info(
            "Weather fetch completed: %d/%d successful",
            success_count,
            len(circuits_to_fetch),
        )

    except Exception as e:
        logger.error("Error in circuit weather fetch: %s", e, exc_info=True)


async def fetch_all_circuits_weather() -> None:
    """Run the batch weather refresh once per process, skipping overlapping triggers."""
    if not config.WEATHER_ENABLED:
        return
    lock = get_loop_lock(_weather_fetch_locks)
    if lock.locked():
        logger.info("Circuit weather fetch already running; skipping overlapping trigger")
        return
    async with lock:
        await _fetch_all_circuits_weather_unlocked()


async def _fetch_single_circuit_weather(
    weather_service: WeatherService, lat: float, lon: float
) -> WeatherData | None:
    """
    Fetch the current weather for a single circuit location.

    Parameters:
        lat (float): Latitude of the circuit.
        lon (float): Longitude of the circuit.

    Returns:
        WeatherData | None: The current weather data on success, `None` if the fetch fails.
    """
    try:
        return await weather_service.get_current_weather(lat, lon)
    except Exception as e:
        logger.debug("Weather fetch failed for (%s, %s): %s", lat, lon, e)
        return None


async def load_weather_from_db() -> None:
    """
    Load weather data from SQLite into in-memory cache.

    Called on startup to restore weather cache from persisted data.
    This ensures weather is available immediately without waiting for
    the first scheduled fetch.
    """
    if not config.WEATHER_ENABLED:
        return

    try:
        db = get_database()
        weather_dict = await db.load_all_circuit_weather()

        if weather_dict:
            count = load_circuit_weather_to_cache(weather_dict)
            logger.info("Loaded %d circuit weather entries from database", count)
        else:
            logger.debug("No cached weather data in database")

        prefetched = await load_prefetched_weather_from_db(db)
        if prefetched:
            logger.info("Loaded %d prefetched next-race weather entries", prefetched)

    except Exception as e:
        logger.warning("Error loading weather from database: %s", e)


async def prefetch_weather() -> None:
    """
    Pre-fetch weather data for next race at :55 each hour.
    Stores in DB cache so image generation at :00 doesn't need API calls.
    """
    if not config.WEATHER_ENABLED:
        logger.debug("Weather disabled, skipping prefetch")
        return

    try:
        db = get_database()
        weather_data = await prefetch_weather_for_next_race(db)
        if weather_data:
            logger.info("Weather prefetch complete: %s", weather_data.temp_display)
        else:
            logger.debug("No weather data prefetched")

        deleted = await db.cleanup_expired_weather_cache()
        if deleted > 0:
            logger.debug("Cleaned up %d expired weather cache entries", deleted)

    except Exception as e:
        logger.error("Error in weather prefetch: %s", e, exc_info=True)
