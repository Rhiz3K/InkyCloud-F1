"""Scheduler service for hourly image generation using static data."""

import asyncio
import functools
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import aiofiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from PIL import Image

from app.config import LANGUAGE_CODES, config
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_keys import get_calendar_image_key, get_teams_image_key
from app.services.renderers import create_renderer
from app.services.version_service import refresh_version_info
from app.services.weather_service import (
    WeatherData,
    WeatherService,
    get_weather_context,
    load_circuit_weather_to_cache,
    load_prefetched_weather_from_db,
    prefetch_weather_for_next_race,
    set_cached_circuit_weather,
)
from app.state import clear_bmp_cache, get_and_clear_api_calls_buffer, requeue_api_calls
from app.utils.async_tasks import run_render
from app.utils.race_times import convert_race_times_to_timezone

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None

# Supported languages for image generation
SUPPORTED_LANGUAGES = list(LANGUAGE_CODES)


def _get_image_key(
    lang: str,
    tz: str | None = None,
    display: str = "1bit",
    weather: str = "off",
) -> str:
    """Build a deterministic image key for generated calendar variants."""
    return get_calendar_image_key(
        lang,
        tz=tz,
        default_timezone=config.DEFAULT_TIMEZONE,
        display=display,
        weather=weather,
    )


def _bmp_to_png(
    bmp_data: bytes, width: int = 400, full_size: bool = False, preserve_color: bool = False
) -> bytes:
    """
    Convert BMP image data to PNG bytes for web previews.

    Parameters:
        bmp_data: Raw BMP image data.
        width: Target width (height scales proportionally).
        full_size: If True, skip resizing.
        preserve_color: If True, keep RGB colors (for spectra6/bwr/bwry).
            Otherwise convert to grayscale.

    Returns:
        PNG image data as bytes.
    """
    with Image.open(BytesIO(bmp_data)) as img_file:
        if preserve_color:
            # Keep colors for multi-color displays
            img: Image.Image = img_file.convert("RGB")
        else:
            # Convert to grayscale for smoother edges (anti-aliasing on resize)
            img = img_file.convert("L")

    if not full_size:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same directory, then atomically replace the target.

    Pregenerated files are rewritten in place every hour while requests serve them
    concurrently; a plain "wb" open truncates first, so a reader could get an empty or
    partial image (and cache it). os.replace guarantees readers see old-or-new, never torn.
    """
    tmp_path = path.with_name(f".{path.name}.tmp")
    async with aiofiles.open(tmp_path, "wb") as f:
        await f.write(data)
    os.replace(tmp_path, path)


def _render_calendar_variant_bytes(
    lang: str,
    display: str,
    race_data: dict,
    historical_data,
    weather_data: WeatherData | None,
    weather_type: str,
) -> bytes:
    """Construct the renderer and render in the worker thread.

    Construction must happen in the SAME thread as the render: renderer __init__ loads
    fonts into a per-thread cache, so building on the event loop would share FreeTypeFont
    objects across render threads.
    """
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang)
    return renderer.render_calendar(race_data, historical_data, weather_data, weather_type)


def _render_teams_variant_bytes(lang: str, display: str, teams_data) -> bytes:
    """Construct the renderer and render the teams screen in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang)
    return renderer.render_teams_drivers(teams_data)


def _render_calendar_preview_pngs(
    lang: str,
    display_name: str,
    race_data: dict,
    historical_data,
    weather_variants: list[tuple[str, WeatherData | None]],
) -> list[tuple[str, bytes]]:
    """Render all calendar preview PNGs for one (lang, display) in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display_name, translator, lang)
    is_color = display_name in {"spectra6", "bwr", "bwry"}

    outputs: list[tuple[str, bytes]] = []
    for weather_type, wd in weather_variants:
        bmp_data = renderer.render_calendar(race_data, historical_data, wd, weather_type)

        suffix = f"_{lang}"
        if display_name != "1bit":
            suffix += f"_{display_name}"
        if weather_type != "off":
            suffix += f"_weather_{weather_type}"

        if display_name == "1bit" and weather_type == "off":
            outputs.append((f"preview_calendar_{lang}.png", _bmp_to_png(bmp_data, width=400)))

        outputs.append(
            (
                f"configure_calendar{suffix}.png",
                _bmp_to_png(bmp_data, full_size=True, preserve_color=is_color),
            )
        )
    return outputs


def _render_teams_preview_pngs(lang: str, display_name: str, teams_data) -> list[tuple[str, bytes]]:
    """Render the teams preview PNGs for one (lang, display) in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display_name, translator, lang)
    is_color = display_name in {"spectra6", "bwr", "bwry"}
    bmp_data = renderer.render_teams_drivers(teams_data)

    outputs: list[tuple[str, bytes]] = []
    if display_name == "1bit":
        outputs.append((f"preview_teams_{lang}.png", _bmp_to_png(bmp_data, width=400)))

    suffix = f"_{lang}"
    if display_name != "1bit":
        suffix += f"_{display_name}"
    outputs.append(
        (
            f"configure_teams{suffix}.png",
            _bmp_to_png(bmp_data, full_size=True, preserve_color=is_color),
        )
    )
    return outputs


async def generate_preview_pngs(race_data: dict | None, historical_data) -> None:
    """
    Generate PNG preview images for landing page.

    Creates small PNG previews (400x240) for each screen type and language.
    These are used on the landing page for screen type selection.

    Args:
        race_data: Next race data from static JSON
        historical_data: Historical race data for the circuit
    """
    from app.services.teams_service import TeamsService, get_default_teams_year

    images_dir = Path(config.IMAGES_PATH)
    images_dir.mkdir(parents=True, exist_ok=True)

    teams_year = get_default_teams_year()
    teams_data = None
    try:
        teams_service = TeamsService()
        teams_data = await teams_service.get_teams_and_drivers(teams_year)
    except Exception as e:
        logger.error("Error fetching teams preview data for %d: %s", teams_year, e)

    weather_variants: list[tuple[str, WeatherData | None]] = [("off", None)]
    if race_data and config.WEATHER_ENABLED:
        _, _, weather_by_type = await get_weather_context(race_data)
        weather_variants = list(weather_by_type.items())

    display_types = ["1bit", "spectra6", "bwr", "bwry"]

    for lang in SUPPORTED_LANGUAGES:
        # Calendar preview - render in the worker thread (construction included), write atomically
        if race_data:
            for display_name in display_types:
                try:
                    outputs = await run_render(
                        functools.partial(
                            _render_calendar_preview_pngs,
                            lang,
                            display_name,
                            race_data,
                            historical_data,
                            weather_variants,
                        )
                    )
                    for filename, png_data in outputs:
                        await _atomic_write_bytes(images_dir / filename, png_data)
                except Exception as e:
                    logger.error(
                        "Error generating calendar preview (%s, %s): %s", lang, display_name, e
                    )

            logger.info("Generated calendar previews for %s", lang)

        # Teams preview
        try:
            if teams_data is None or not teams_data.teams:
                logger.warning(
                    "Skipping teams previews for %s: no teams data for %d", lang, teams_year
                )
                continue

            written_paths: list[Path] = []
            for display_name in display_types:
                outputs = await run_render(
                    functools.partial(_render_teams_preview_pngs, lang, display_name, teams_data)
                )
                for filename, png_data in outputs:
                    path = images_dir / filename
                    await _atomic_write_bytes(path, png_data)
                    written_paths.append(path)

            logger.info("Generated teams previews for %s: %s", lang, written_paths)
        except Exception as e:
            logger.error("Error generating teams preview (%s): %s", lang, e)


async def _generate_variant(
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    weather_data: WeatherData | None,
    lang: str,
    tz: str | None,
    display: str,
    weather_type: str,
) -> Path | None:
    """Render and save a single pregenerated calendar variant.

    Returns the written path on success, or None if this variant failed — so one bad
    render never propagates up and aborts the whole generation run.
    """
    wd = weather_data if weather_type != "off" else None
    image_key = _get_image_key(lang, tz, display, weather_type)
    image_path = images_dir / f"{image_key}.bmp"

    try:
        # Rendering is CPU-bound (Pillow + pure-Python palette/packing); run it on the render
        # executor, constructing the renderer in the worker so fonts stay thread-local.
        bmp_data = await run_render(
            functools.partial(
                _render_calendar_variant_bytes,
                lang,
                display,
                race_data,
                historical_data,
                wd,
                weather_type,
            )
        )
        await _atomic_write_bytes(image_path, bmp_data)
        await db.save_generated_image(image_key=image_key, image_path=str(image_path), lang=lang)
    except Exception as exc:
        logger.error(
            "Error generating calendar variant (lang=%s, tz=%s, %s, %s): %s",
            lang,
            tz,
            display,
            weather_type,
            exc,
            exc_info=True,
        )
        return None
    return image_path


def _delete_stale_bmps(images_dir: Path, *, keep: set[Path]) -> int:
    """Delete pregenerated BMPs not (re)written this run.

    Called only after a fully successful run, so the remaining stale files are variants that
    are genuinely no longer produced (e.g. a timezone that dropped out of popularity, or last
    season's teams files) rather than casualties of a mid-run failure.
    """
    keep_names = {p.name for p in keep}
    removed = 0
    for bmp_file in images_dir.glob("*.bmp"):
        if bmp_file.name not in keep_names:
            bmp_file.unlink()
            removed += 1
    return removed


def _load_historical_data(race_data: dict) -> object | None:
    """Load historical race data for the current circuit when available."""
    circuit_id = race_data.get("circuit", {}).get("circuitId", "")
    if not circuit_id:
        return None

    historical_data = F1Service.get_historical_from_static(circuit_id)
    if historical_data is None:
        return None

    if getattr(historical_data, "is_new_track", False):
        logger.info("Circuit %s: new track (no historical data)", circuit_id)
    else:
        logger.info(
            "Circuit %s: historical data from %s",
            circuit_id,
            getattr(historical_data, "season", "unknown"),
        )

    return historical_data


async def _load_weather_context(
    race_data: dict,
) -> tuple[WeatherData | None, WeatherData | None, dict[str, WeatherData | None]]:
    """Load current and race-day weather variants for generation."""
    current_weather, race_weather, weather_by_type = await get_weather_context(race_data)

    circuit_id = race_data.get("circuit", {}).get("circuitId")
    if circuit_id and current_weather:
        logger.info("Current weather for %s: %s", circuit_id, current_weather.temp_display)
    if circuit_id and race_weather:
        logger.info("Race-day weather for %s: %s", circuit_id, race_weather.temp_display)

    return current_weather, race_weather, weather_by_type


async def _generate_base_variants(
    *,
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    display_types: list[str],
    weather_by_type: dict[str, WeatherData | None],
) -> tuple[set[Path], int]:
    """Generate the base language/display/weather combinations.

    Returns (written paths, failure count).
    """
    generated: set[Path] = set()
    failures = 0

    for lang in SUPPORTED_LANGUAGES:
        for display in display_types:
            for weather_type, wd in weather_by_type.items():
                path = await _generate_variant(
                    images_dir,
                    db,
                    race_data,
                    historical_data,
                    wd,
                    lang,
                    None,
                    display,
                    weather_type,
                )
                if path is not None:
                    generated.add(path)
                else:
                    failures += 1

    return generated, failures


async def _generate_popular_tz_variants(
    *,
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    display_types: list[str],
    weather_by_type: dict[str, WeatherData | None],
) -> tuple[set[Path], int]:
    """Generate extra calendar variants for the most-used non-default timezones.

    Returns (written paths, failure count).
    """
    generated: set[Path] = set()
    failures = 0

    popular_variants = await db.get_popular_tz_variants(
        min_requests=10, hours=24, limit=20, exclude_tz=config.DEFAULT_TIMEZONE
    )
    if not popular_variants:
        return generated, failures

    logger.info("Generating %d popular TZ variants", len(popular_variants))

    for variant in popular_variants:
        lang = variant["lang"]
        tz = variant["tz"]
        if lang not in SUPPORTED_LANGUAGES:
            logger.debug("Skipping unsupported language: %s", lang)
            continue

        try:
            race_data_converted = convert_race_times_to_timezone(race_data, tz)
        except Exception as exc:
            # One bad DB-sourced tz must not abort the rest of the run (and everything after it).
            failures += 1
            logger.error("Error converting race times to %s: %s", tz, exc, exc_info=True)
            continue

        for display in display_types:
            for weather_type, wd in weather_by_type.items():
                path = await _generate_variant(
                    images_dir,
                    db,
                    race_data_converted,
                    historical_data,
                    wd,
                    lang,
                    tz,
                    display,
                    weather_type,
                )
                if path is not None:
                    generated.add(path)
                else:
                    failures += 1

    return generated, failures


async def _generate_teams_bmp_variants(
    *,
    images_dir: Path,
    db: Database,
) -> tuple[set[Path], int]:
    """Generate pregenerated teams BMPs for all languages and display modes.

    Returns (written paths, failure count). A failed/empty upstream fetch counts as a failure
    so the caller skips stale pruning and keeps the previous teams BMPs in place.
    """
    from app.services.teams_service import TeamsService, get_default_teams_year

    teams_year = get_default_teams_year()
    teams_service = TeamsService()
    try:
        teams_data = await teams_service.get_teams_and_drivers(teams_year)
    except Exception as exc:
        logger.error("Error fetching teams BMP data for %d: %s", teams_year, exc, exc_info=True)
        return set(), 1

    if not teams_data.teams:
        logger.warning("Skipping teams BMP generation: no teams data for %d", teams_year)
        return set(), 1

    generated: set[Path] = set()
    failures = 0
    display_variants = ["1bit", "spectra6", "bwr", "bwry"]

    for lang in SUPPORTED_LANGUAGES:
        for display in display_variants:
            try:
                bmp_data = await run_render(
                    functools.partial(_render_teams_variant_bytes, lang, display, teams_data)
                )
                image_key = get_teams_image_key(lang, teams_year, display=display)
                image_path = images_dir / f"{image_key}.bmp"

                await _atomic_write_bytes(image_path, bmp_data)

                await db.save_generated_image(
                    image_key=image_key,
                    image_path=str(image_path),
                    lang=lang,
                    season=teams_year,
                )
                generated.add(image_path)
            except Exception as exc:
                failures += 1
                logger.error(
                    "Error generating teams BMP (%s, %s, %d): %s",
                    lang,
                    display,
                    teams_year,
                    exc,
                    exc_info=True,
                )

    logger.info(
        "Generated teams BMP variants for %d languages x %d displays (%d total, %d failed)",
        len(SUPPORTED_LANGUAGES),
        len(display_variants),
        len(generated),
        failures,
    )
    return generated, failures


async def collect_and_generate() -> None:
    """Generate pregenerated calendar and teams BMP variants from static data."""
    logger.info("Starting image generation from static data")

    try:
        db = Database()
        try:
            f1_service = F1Service()

            images_dir = Path(config.IMAGES_PATH)
            images_dir.mkdir(parents=True, exist_ok=True)

            race_data = f1_service.get_next_race_from_static()
            if not race_data:
                logger.warning("No upcoming race found in static data")
                return

            logger.info("Next race: %s (from static data)", race_data.get("race_name"))

            historical_data = _load_historical_data(race_data)

            _, _, weather_by_type = await _load_weather_context(race_data)

            # When weather is enabled but no live data came back (open-meteo outage), the
            # weather-variant BMPs simply aren't produced this run. Treat that as degraded so
            # the stale prune doesn't delete every previously-good *_weather_* file.
            weather_degraded = config.WEATHER_ENABLED and len(weather_by_type) <= 1

            display_types = ["1bit", "spectra6", "bwr", "bwry"]
            logger.info(
                "Generating variants: displays=%s, weather=%s",
                display_types,
                list(weather_by_type.keys()),
            )

            # Write every variant first (overwriting its deterministic path), then prune stale
            # files. We never delete up front: a mid-run failure used to leave devices with no
            # pregenerated images for an hour.
            generated_paths: set[Path] = set()
            total_failures = 0

            base_paths, base_failures = await _generate_base_variants(
                images_dir=images_dir,
                db=db,
                race_data=race_data,
                historical_data=historical_data,
                display_types=display_types,
                weather_by_type=weather_by_type,
            )
            generated_paths |= base_paths
            total_failures += base_failures

            tz_paths, tz_failures = await _generate_popular_tz_variants(
                images_dir=images_dir,
                db=db,
                race_data=race_data,
                historical_data=historical_data,
                display_types=display_types,
                weather_by_type=weather_by_type,
            )
            generated_paths |= tz_paths
            total_failures += tz_failures

            await generate_preview_pngs(race_data, historical_data)

            teams_paths, teams_failures = await _generate_teams_bmp_variants(
                images_dir=images_dir, db=db
            )
            generated_paths |= teams_paths
            total_failures += teams_failures

            # Only prune when the whole run succeeded (and weather wasn't degraded), so a
            # transient failure never deletes a previously-good file devices still serve.
            if total_failures == 0 and not weather_degraded:
                removed = _delete_stale_bmps(images_dir, keep=generated_paths)
                if removed:
                    logger.info("Pruned %d stale BMP files", removed)
            else:
                logger.warning(
                    "Skipping stale-BMP prune: %d variant failure(s), weather_degraded=%s",
                    total_failures,
                    weather_degraded,
                )

            await db.set_cache_meta("last_generation", datetime.now(timezone.utc).isoformat())
            clear_bmp_cache()

            if config.STATS_RETENTION_DAYS > 0:
                await db.cleanup_old_stats(days=config.STATS_RETENTION_DAYS)

            logger.info(
                "Image generation completed: %d images (%d failures)",
                len(generated_paths),
                total_failures,
            )
        finally:
            await db.close()

    except Exception as exc:
        logger.error("Error in image generation: %s", exc, exc_info=True)


async def flush_api_calls_to_db() -> None:
    """
    Flush API calls buffer to SQLite.

    This job runs every minute to persist API call data from
    the in-memory buffer to the database.
    """
    calls = get_and_clear_api_calls_buffer()
    if not calls:
        return
    db = Database()
    try:
        try:
            count = await db.save_api_calls_batch(calls)
        except asyncio.CancelledError:
            # APScheduler's shutdown cancels in-flight jobs; put the batch back so the
            # lifespan's final flush can persist it instead of silently dropping it.
            requeue_api_calls(calls)
            raise
        except Exception as e:
            # Re-queue ONLY on save failure — a post-commit cleanup error must not re-queue
            # an already-persisted batch (that would double-count stats).
            requeue_api_calls(calls)
            logger.error(
                "Error flushing API calls (re-queued %d): %s", len(calls), e, exc_info=True
            )
        else:
            logger.debug("Flushed %d API calls to database", count)
    finally:
        try:
            await db.close()
        except Exception as e:
            logger.warning("Error closing database after API-call flush: %s", e)


async def fetch_all_circuits_weather() -> None:
    """
    Fetch weather for all F1 circuits, cache in memory, and persist to DB.

    Iterates circuits from current season, fetches weather sequentially with
    1s pause, stores in cache and SQLite. Retries failed circuits up to 10x.
    Returns immediately if weather is disabled.
    """
    if not config.WEATHER_ENABLED:
        logger.debug("Weather is disabled, skipping fetch")
        return

    logger.info("Starting circuit weather fetch")

    try:
        db = Database()
        try:
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
            max_attempts = 10

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

            # Retry rounds (attempts 2-10)
            for round_num in range(2, max_attempts + 1):
                if not failed:
                    break

                logger.debug(
                    "Weather retry round %d, %d circuits remaining", round_num, len(failed)
                )
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
        finally:
            await db.close()

    except Exception as e:
        logger.error("Error in circuit weather fetch: %s", e, exc_info=True)


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
        db = Database()
        try:
            weather_dict = await db.load_all_circuit_weather()

            if weather_dict:
                count = load_circuit_weather_to_cache(weather_dict)
                logger.info("Loaded %d circuit weather entries from database", count)
            else:
                logger.debug("No cached weather data in database")

            prefetched = await load_prefetched_weather_from_db(db)
            if prefetched:
                logger.info("Loaded %d prefetched next-race weather entries", prefetched)
        finally:
            await db.close()

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
        db = Database()
        try:
            weather_data = await prefetch_weather_for_next_race(db)
            if weather_data:
                logger.info("Weather prefetch complete: %s", weather_data.temp_display)
            else:
                logger.debug("No weather data prefetched")

            deleted = await db.cleanup_expired_weather_cache()
            if deleted > 0:
                logger.debug("Cleaned up %d expired weather cache entries", deleted)
        finally:
            await db.close()
    except Exception as e:
        logger.error("Error in weather prefetch: %s", e, exc_info=True)


def _run_backup() -> None:
    """
    Trigger database backup to S3 if configured.

    Returns immediately if backup not configured.
    """
    from app.services.backup import is_backup_configured, perform_backup

    if not is_backup_configured():
        return

    perform_backup()


def _parse_cron_expression(cron_expr: str) -> dict:
    """
    Parse a cron expression into APScheduler CronTrigger kwargs.

    Args:
        cron_expr: Standard cron expression (minute hour day month day_of_week)

    Returns:
        Dictionary of kwargs for CronTrigger
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        logger.warning("Invalid cron expression '%s', using default '0 3 * * *'", cron_expr)
        parts = ["0", "3", "*", "*", "*"]

    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def _register_backup_job(sched: AsyncIOScheduler) -> None:
    """
    Register the backup job if backup is configured and enabled.

    Args:
        sched: The AsyncIOScheduler instance to add the job to.
    """
    from app.services.backup import is_backup_configured

    if not is_backup_configured():
        logger.info("S3 backup not configured or disabled")
        return

    cron_kwargs = _parse_cron_expression(config.BACKUP_CRON)

    sched.add_job(
        _run_backup,
        trigger=CronTrigger(**cron_kwargs),
        id="s3_backup",
        name=f"S3 database backup (cron: {config.BACKUP_CRON})",
        replace_existing=True,
    )

    logger.info("S3 backup job registered (cron: %s)", config.BACKUP_CRON)


def start_scheduler() -> None:
    """
    Initialize and start the background scheduler with all jobs.

    Jobs: hourly image gen (:00), API flush (every min), weather (:55 if
    enabled), backup (if configured), version refresh (hourly at :05).
    Returns if scheduler disabled or already running.
    """
    global scheduler  # skipcq: PYL-W0603 - singleton pattern for scheduler instance

    if not config.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled")
        return

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    # misfire_grace_time defaults to 1s in APScheduler; under render bursts or GC the event
    # loop can be delayed past that, silently skipping whole cron runs (stale images for an
    # hour, no backup for a day). Give jobs a generous grace window and coalesce missed runs.
    scheduler = AsyncIOScheduler(
        job_defaults={
            "misfire_grace_time": 300,
            "coalesce": True,
            "max_instances": 1,
        }
    )

    # Weather prefetch at :55 (before image generation at :00)
    if config.WEATHER_ENABLED:
        scheduler.add_job(
            prefetch_weather,
            trigger=CronTrigger(minute=55),
            id="weather_prefetch",
            name="Weather data prefetch for next race",
            replace_existing=True,
        )

    # Hourly: Regenerate images from static data
    scheduler.add_job(
        collect_and_generate,
        trigger=CronTrigger(minute=0),
        id="hourly_generation",
        name="Hourly image generation from static data",
        replace_existing=True,
    )

    # Every minute: Flush API calls buffer to database
    scheduler.add_job(
        flush_api_calls_to_db,
        trigger=CronTrigger(second=0),
        id="flush_api_calls",
        name="Flush API calls to database",
        replace_existing=True,
    )

    # Hourly at :55: Fetch weather for all circuits (before image generation at :00)
    if config.WEATHER_ENABLED:
        scheduler.add_job(
            fetch_all_circuits_weather,
            trigger=CronTrigger(minute=55),
            id="fetch_circuit_weather",
            name="Fetch weather for all circuits",
            replace_existing=True,
        )

    # Conditional: S3 database backup
    _register_backup_job(scheduler)

    # Hourly at :05: Refresh version info from GitHub API
    scheduler.add_job(
        refresh_version_info,
        trigger=CronTrigger(minute=5),
        id="refresh_version_info",
        name="Refresh version info from GitHub (hourly)",
        replace_existing=True,
    )

    scheduler.start()
    weather_info = ", weather at :55" if config.WEATHER_ENABLED else ""
    logger.info(
        "Scheduler started - generation at :00%s, API flush every min, version at :05",
        weather_info,
    )


def stop_scheduler() -> None:
    """Stop the background scheduler and wait for in-flight jobs to finish."""
    global scheduler  # skipcq: PYL-W0603 - singleton pattern for scheduler instance

    if scheduler is not None:
        scheduler.shutdown(wait=True)
        scheduler = None
        logger.info("Scheduler stopped")


async def run_initial_generation() -> None:
    """
    Perform startup: load weather, fetch fresh weather, generate images, refresh version.

    Failures in individual steps are logged but don't stop subsequent steps.
    """
    logger.info("Running initial generation from static data")

    if config.WEATHER_ENABLED:
        try:
            await load_weather_from_db()
        except Exception as e:
            logger.warning("Error loading weather from database: %s", e)

        try:
            await fetch_all_circuits_weather()
        except Exception as e:
            logger.error("Error fetching initial weather: %s", e, exc_info=True)

    try:
        await collect_and_generate()
    except Exception as e:
        logger.error("Error in initial generation: %s", e, exc_info=True)

    # 4. Refresh version info
    try:
        await refresh_version_info()
        logger.info("Version info refreshed on startup")
    except Exception as e:
        logger.error("Error refreshing version info on startup: %s", e, exc_info=True)


# Legacy function names for backwards compatibility
sync_full_season = collect_and_generate
sync_season_to_db = collect_and_generate
