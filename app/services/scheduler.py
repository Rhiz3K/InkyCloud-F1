"""Scheduler service for hourly image generation using static data."""

import asyncio
import copy
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import aiofiles
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from PIL import Image

from app.config import config
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.version_service import refresh_version_info
from app.services.weather_service import (
    WeatherData,
    WeatherService,
    load_circuit_weather_to_cache,
    set_cached_circuit_weather,
)
from app.state import clear_bmp_cache, get_and_clear_api_calls_buffer

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None

# Supported languages for image generation
SUPPORTED_LANGUAGES = ["en", "cs"]


def _get_image_key(lang: str, tz: str | None = None) -> str:
    """
    Generate image key for file naming.

    Args:
        lang: Language code (e.g., "en", "cs")
        tz: Optional timezone (e.g., "America/New_York")

    Returns:
        Image key for filename (e.g., "calendar_en", "calendar_en_America_New_York")
    """
    key = f"calendar_{lang}"
    if tz and tz != config.DEFAULT_TIMEZONE:
        # Replace / with _ for filesystem safety
        tz_safe = tz.replace("/", "_")
        key += f"_{tz_safe}"
    return key


def _convert_race_times_to_timezone(race_data: dict, target_tz_str: str) -> dict:
    """
    Convert race schedule times to a different timezone.

    Args:
        race_data: Race data dictionary with schedule
        target_tz_str: Target timezone string (e.g., 'America/New_York')

    Returns:
        Race data with converted schedule times
    """
    try:
        target_tz = pytz.timezone(target_tz_str)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone {target_tz_str}, returning original data")
        return race_data

    # Deep copy to avoid modifying original
    result = copy.deepcopy(race_data)

    # Convert schedule times
    schedule = result.get("schedule", [])
    for event in schedule:
        iso_str = event.get("datetime")
        if iso_str:
            try:
                # Parse ISO datetime string
                dt = datetime.fromisoformat(iso_str)
                # Convert to target timezone
                dt_local = dt.astimezone(target_tz)
                # Update both datetime and display_time
                event["datetime"] = dt_local.isoformat()
                event["display_time"] = dt_local.strftime("%a %H:%M")
            except (ValueError, TypeError) as e:
                logger.warning("Error converting time %s: %s", iso_str, e)

    # Update race_date to target timezone format
    if schedule:
        for event in schedule:
            if event.get("name") == "Race":
                iso_str = event.get("datetime")
                if iso_str:
                    try:
                        dt = datetime.fromisoformat(iso_str)
                        result["race_date"] = dt.strftime("%d.%m.%Y")
                    except (ValueError, TypeError):
                        pass
                break

    # Update timezone field
    result["timezone"] = target_tz_str

    return result


def _bmp_to_png(bmp_data: bytes, width: int = 400, full_size: bool = False) -> bytes:
    """
    Convert BMP image data to PNG bytes for web previews.

    Parameters:
        bmp_data: Raw BMP image data.
        width: Target width (height scales proportionally).
        full_size: If True, skip resizing.

    Returns:
        PNG image data as bytes.
    """
    img_file = Image.open(BytesIO(bmp_data))

    # Convert to grayscale for smoother edges (anti-aliasing on resize)
    img: Image.Image = img_file.convert("L")

    if not full_size:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def generate_preview_pngs(race_data: dict | None, historical_data) -> None:
    """
    Generate PNG preview images for landing page.

    Creates small PNG previews (400x240) for each screen type and language.
    These are used on the landing page for screen type selection.

    Args:
        race_data: Next race data from static JSON
        historical_data: Historical race data for the circuit
    """
    from app.services.teams_service import TeamsService

    images_dir = Path(config.IMAGES_PATH)
    images_dir.mkdir(parents=True, exist_ok=True)

    for lang in SUPPORTED_LANGUAGES:
        translator = get_translator(lang)
        renderer = Renderer(translator)

        # Calendar preview
        if race_data:
            try:
                bmp_data = renderer.render_calendar(race_data, historical_data)

                homepage_png = _bmp_to_png(bmp_data, width=400)
                homepage_path = images_dir / f"preview_calendar_{lang}.png"
                async with aiofiles.open(homepage_path, "wb") as f:
                    await f.write(homepage_png)

                configure_png = _bmp_to_png(bmp_data, full_size=True)
                configure_path = images_dir / f"configure_calendar_{lang}.png"
                async with aiofiles.open(configure_path, "wb") as f:
                    await f.write(configure_png)

                logger.info(f"Generated calendar previews: {homepage_path}, {configure_path}")
            except Exception as e:
                logger.error(f"Error generating calendar preview ({lang}): {e}")

        # Teams preview
        try:
            teams_service = TeamsService()
            teams_data = await teams_service.get_teams_and_drivers()
            bmp_data = renderer.render_teams_drivers(teams_data)

            homepage_png = _bmp_to_png(bmp_data, width=400)
            homepage_path = images_dir / f"preview_teams_{lang}.png"
            async with aiofiles.open(homepage_path, "wb") as f:
                await f.write(homepage_png)

            configure_png = _bmp_to_png(bmp_data, full_size=True)
            configure_path = images_dir / f"configure_teams_{lang}.png"
            async with aiofiles.open(configure_path, "wb") as f:
                await f.write(configure_png)

            logger.info(f"Generated teams previews: {homepage_path}, {configure_path}")
        except Exception as e:
            logger.error(f"Error generating teams preview ({lang}): {e}")


async def collect_and_generate() -> None:
    """
    Generate pre-rendered BMP images from static data.

    This job runs every hour to:
    1. Delete all existing BMP files in IMAGES_PATH
    2. Get next race from static JSON data (no API call)
    3. Get historical data from static JSON (no API call)
    4. Generate default BMP images for all supported languages (default TZ)
    5. Generate popular timezone variants based on usage stats (max 20)
    """
    logger.info("Starting image generation from static data")

    try:
        db = Database()
        f1_service = F1Service()

        # 1. Delete all existing BMP files
        images_dir = Path(config.IMAGES_PATH)
        images_dir.mkdir(parents=True, exist_ok=True)

        deleted_count = 0
        for bmp_file in images_dir.glob("*.bmp"):
            bmp_file.unlink()
            deleted_count += 1

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} existing BMP files")

        # 2. Get next race from static data (NO API CALL)
        race_data = f1_service.get_next_race_from_static()

        if not race_data:
            logger.warning("No upcoming race found in static data")
            return

        logger.info(f"Next race: {race_data.get('race_name')} (from static data)")

        # 3. Get historical data from static JSON (NO API CALL)
        circuit_id = race_data.get("circuit", {}).get("circuitId", "")
        historical_data = None

        if circuit_id:
            historical_data = F1Service.get_historical_from_static(circuit_id)

            if historical_data.is_new_track:
                logger.info(f"Circuit {circuit_id}: new track (no historical data)")
            else:
                logger.info(f"Circuit {circuit_id}: historical data from {historical_data.season}")

        # 4. Generate default images for all languages (default timezone)
        generated_count = 0
        for lang in SUPPORTED_LANGUAGES:
            translator = get_translator(lang)
            renderer = Renderer(translator)

            # Generate image with default timezone
            bmp_data = renderer.render_calendar(race_data, historical_data)

            # Save image using async file I/O
            image_key = _get_image_key(lang)
            image_path = images_dir / f"{image_key}.bmp"

            async with aiofiles.open(image_path, "wb") as f:
                await f.write(bmp_data)

            # Record in database
            await db.save_generated_image(
                image_key=image_key, image_path=str(image_path), lang=lang
            )

            logger.info(f"Generated default image: {image_path}")
            generated_count += 1

        # 5. Generate popular timezone variants (max 20)
        popular_variants = await db.get_popular_tz_variants(
            min_requests=10, hours=24, limit=20, exclude_tz=config.DEFAULT_TIMEZONE
        )

        if popular_variants:
            logger.info(f"Generating {len(popular_variants)} popular TZ variants")

            for variant in popular_variants:
                lang = variant["lang"]
                tz = variant["tz"]
                count = variant["count"]

                # Skip if language not supported
                if lang not in SUPPORTED_LANGUAGES:
                    logger.debug(f"Skipping unsupported language: {lang}")
                    continue

                # Convert race times to target timezone
                race_data_converted = _convert_race_times_to_timezone(race_data, tz)

                # Generate image
                translator = get_translator(lang)
                renderer = Renderer(translator)
                bmp_data = renderer.render_calendar(race_data_converted, historical_data)

                # Save image
                image_key = _get_image_key(lang, tz)
                image_path = images_dir / f"{image_key}.bmp"

                async with aiofiles.open(image_path, "wb") as f:
                    await f.write(bmp_data)

                # Record in database
                await db.save_generated_image(
                    image_key=image_key, image_path=str(image_path), lang=lang
                )

                logger.info(f"Generated TZ variant: {image_path} ({count} requests/24h)")
                generated_count += 1
        else:
            logger.debug("No popular TZ variants to generate")

        # Update last run timestamp
        await db.set_cache_meta("last_generation", datetime.now(timezone.utc).isoformat())

        # Clear in-memory BMP cache after regeneration
        clear_bmp_cache()

        # Cleanup old hourly stats (keep 30 days) - legacy table
        await db.cleanup_old_stats(days=30)

        # 6. Generate PNG previews for landing page
        await generate_preview_pngs(race_data, historical_data)

        logger.info(f"Image generation completed: {generated_count} images (0 API calls)")

    except Exception as e:
        logger.error(f"Error in image generation: {e}", exc_info=True)


async def flush_api_calls_to_db() -> None:
    """
    Flush API calls buffer to SQLite.

    This job runs every minute to persist API call data from
    the in-memory buffer to the database.
    """
    try:
        calls = get_and_clear_api_calls_buffer()
        if calls:
            db = Database()
            count = await db.save_api_calls_batch(calls)
            logger.debug("Flushed %d API calls to database", count)
    except Exception as e:
        logger.error("Error flushing API calls: %s", e, exc_info=True)


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
                    logger.debug("Weather fetched for %s on attempt %d", circuit["id"], round_num)
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
            "Weather fetch completed: %d/%d successful", success_count, len(circuits_to_fetch)
        )

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
        weather_dict = await db.load_all_circuit_weather()

        if weather_dict:
            count = load_circuit_weather_to_cache(weather_dict)
            logger.info("Loaded %d circuit weather entries from database", count)
        else:
            logger.debug("No cached weather data in database")

    except Exception as e:
        logger.warning("Error loading weather from database: %s", e)


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
        logger.warning(f"Invalid cron expression '{cron_expr}', using default '0 3 * * *'")
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

    logger.info(f"S3 backup job registered (cron: {config.BACKUP_CRON})")


def start_scheduler() -> None:
    """
    Initialize and start the background scheduler with all jobs.

    Jobs: hourly image gen (:00), API flush (every min), weather (:55 if
    enabled), backup (if configured), version refresh (00:05 daily).
    Returns if scheduler disabled or already running.
    """
    global scheduler

    if not config.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled")
        return

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    scheduler = AsyncIOScheduler()

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

    # Midnight: Refresh version info from GitHub API
    scheduler.add_job(
        refresh_version_info,
        trigger=CronTrigger(hour=0, minute=5),
        id="refresh_version_info",
        name="Refresh version info from GitHub",
        replace_existing=True,
    )

    scheduler.start()
    weather_info = ", weather at :55" if config.WEATHER_ENABLED else ""
    logger.info(
        f"Scheduler started - generation at :00{weather_info}, API flush every min, "
        "version at 00:05"
    )


def stop_scheduler() -> None:
    """Stop the background scheduler."""
    global scheduler

    if scheduler is not None:
        scheduler.shutdown()
        scheduler = None
        logger.info("Scheduler stopped")


async def run_initial_generation() -> None:
    """
    Perform startup: load weather, fetch fresh weather, generate images, refresh version.

    Failures in individual steps are logged but don't stop subsequent steps.
    """
    logger.info("Running initial generation from static data")

    # 1. Load weather from SQLite first (instant, provides fallback data)
    try:
        await load_weather_from_db()
    except Exception as e:
        logger.warning("Error loading weather from database: %s", e)

    # 2. Fetch fresh weather data (before image generation)
    try:
        await fetch_all_circuits_weather()
    except Exception as e:
        logger.error("Error fetching initial weather: %s", e, exc_info=True)

    # 3. Generate images (now with weather data available)
    try:
        await collect_and_generate()
    except Exception as e:
        logger.error(f"Error in initial generation: {e}", exc_info=True)

    # 4. Refresh version info
    try:
        await refresh_version_info()
        logger.info("Version info refreshed on startup")
    except Exception as e:
        logger.error(f"Error refreshing version info on startup: {e}", exc_info=True)


# Legacy function names for backwards compatibility
sync_full_season = collect_and_generate
sync_season_to_db = collect_and_generate
