"""Scheduler service for hourly image generation using static data."""

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
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.version_service import refresh_version_info
from app.services.weather_service import (
    WeatherData,
    get_cached_weather_from_db,
    prefetch_weather_for_next_race,
)

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None

# Supported languages for image generation
SUPPORTED_LANGUAGES = ["en", "cs"]


def _get_image_key(
    lang: str,
    tz: str | None = None,
    display: str = "1bit",
    weather: str = "off",
) -> str:
    key = f"calendar_{lang}"
    if tz and tz != config.DEFAULT_TIMEZONE:
        tz_safe = tz.replace("/", "_")
        key += f"_{tz_safe}"
    if display == "spectra6":
        key += "_spectra6"
    if weather != "off":
        key += f"_weather_{weather}"
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
                logger.warning(f"Error converting time {iso_str}: {e}")

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
    Convert BMP to PNG for web previews.

    Uses grayscale mode for better anti-aliasing on resize,
    resulting in smoother edges compared to 1-bit mode.

    Args:
        bmp_data: Raw BMP image data
        width: Target width (height calculated to maintain aspect ratio)
        full_size: If True, skip resize and keep original 800x480

    Returns:
        PNG image data as bytes
    """
    img = Image.open(BytesIO(bmp_data))

    # Convert to grayscale for smoother edges (anti-aliasing on resize)
    img = img.convert("L")

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
) -> bool:
    translator = get_translator(lang)
    if display == "spectra6":
        renderer = Spectra6Renderer(translator)
    else:
        renderer = Renderer(translator)

    wd = weather_data if weather_type != "off" else None
    bmp_data = renderer.render_calendar(race_data, historical_data, wd)

    image_key = _get_image_key(lang, tz, display, weather_type)
    image_path = images_dir / f"{image_key}.bmp"

    async with aiofiles.open(image_path, "wb") as f:
        await f.write(bmp_data)

    await db.save_generated_image(image_key=image_key, image_path=str(image_path), lang=lang)
    return True


async def collect_and_generate() -> None:
    logger.info("Starting image generation from static data")

    try:
        db = Database()
        f1_service = F1Service()

        images_dir = Path(config.IMAGES_PATH)
        images_dir.mkdir(parents=True, exist_ok=True)

        deleted_count = 0
        for bmp_file in images_dir.glob("*.bmp"):
            bmp_file.unlink()
            deleted_count += 1

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} existing BMP files")

        race_data = f1_service.get_next_race_from_static()
        if not race_data:
            logger.warning("No upcoming race found in static data")
            return

        logger.info(f"Next race: {race_data.get('race_name')} (from static data)")

        circuit_id = race_data.get("circuit", {}).get("circuitId", "")
        historical_data = None
        if circuit_id:
            historical_data = F1Service.get_historical_from_static(circuit_id)

        current_weather: WeatherData | None = None
        race_weather: WeatherData | None = None
        race_weather_available = False

        if config.WEATHER_ENABLED:
            circuit = race_data.get("circuit", {})
            lat = circuit.get("lat")
            lon = circuit.get("long")
            if lat and lon:
                try:
                    lat_f, lon_f = round(float(lat), 2), round(float(lon), 2)
                    current_key = f"current_{lat_f}_{lon_f}"
                    current_weather = await get_cached_weather_from_db(db, current_key)
                    if current_weather:
                        logger.info("Current weather: %s", current_weather.temp_display)

                    schedule = race_data.get("schedule", [])
                    race_session = next((s for s in schedule if s.get("name") == "Race"), None)
                    if race_session:
                        race_dt_str = race_session.get("datetime")
                        if race_dt_str:
                            race_dt = datetime.fromisoformat(race_dt_str)
                            days_until = (race_dt - datetime.now(timezone.utc)).days
                            if 0 <= days_until <= 14:
                                race_key = f"{lat_f}_{lon_f}_{race_dt.isoformat()}"
                                race_weather = await get_cached_weather_from_db(db, race_key)
                                if race_weather:
                                    race_weather_available = True
                                    logger.info("Race weather: %s", race_weather.temp_display)
                except (ValueError, TypeError) as e:
                    logger.warning("Weather error: %s", e)

        display_types = ["1bit", "spectra6"]
        weather_types = ["off"]
        if config.WEATHER_ENABLED and current_weather:
            weather_types.append("current")
        if race_weather_available and race_weather:
            weather_types.append("race")

        logger.info(f"Generating variants: displays={display_types}, weather={weather_types}")

        generated_count = 0
        for lang in SUPPORTED_LANGUAGES:
            for display in display_types:
                for weather_type in weather_types:
                    wd = None
                    if weather_type == "current":
                        wd = current_weather
                    elif weather_type == "race":
                        wd = race_weather

                    if await _generate_variant(
                        images_dir,
                        db,
                        race_data,
                        historical_data,
                        wd,
                        lang,
                        None,
                        display,
                        weather_type,
                    ):
                        generated_count += 1

        popular_variants = await db.get_popular_tz_variants(
            min_requests=10, hours=24, limit=20, exclude_tz=config.DEFAULT_TIMEZONE
        )

        if popular_variants:
            logger.info(f"Generating {len(popular_variants)} popular TZ variants")
            for variant in popular_variants:
                lang = variant["lang"]
                tz = variant["tz"]
                if lang not in SUPPORTED_LANGUAGES:
                    continue

                race_data_converted = _convert_race_times_to_timezone(race_data, tz)

                for display in display_types:
                    for weather_type in weather_types:
                        wd = None
                        if weather_type == "current":
                            wd = current_weather
                        elif weather_type == "race":
                            wd = race_weather

                        if await _generate_variant(
                            images_dir,
                            db,
                            race_data_converted,
                            historical_data,
                            wd,
                            lang,
                            tz,
                            display,
                            weather_type,
                        ):
                            generated_count += 1

        await db.set_cache_meta("last_generation", datetime.now(timezone.utc).isoformat())

        try:
            from app.main import clear_bmp_cache

            clear_bmp_cache()
        except ImportError:
            pass

        await db.cleanup_old_stats(days=30)
        await generate_preview_pngs(race_data, historical_data)

        logger.info(f"Image generation completed: {generated_count} images")

    except Exception as e:
        logger.error(f"Error in image generation: {e}", exc_info=True)


async def flush_api_calls_to_db() -> None:
    """
    Flush API calls buffer to SQLite.

    This job runs every minute to persist API call data from
    the in-memory buffer to the database.
    """
    try:
        from app.main import get_and_clear_api_calls_buffer

        calls = get_and_clear_api_calls_buffer()
        if calls:
            db = Database()
            count = await db.save_api_calls_batch(calls)
            logger.debug(f"Flushed {count} API calls to database")
    except ImportError:
        pass  # Buffer not available (e.g., during tests)
    except Exception as e:
        logger.error(f"Error flushing API calls: {e}", exc_info=True)


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


def _run_backup() -> None:
    """
    Run database backup to S3 (synchronous wrapper for scheduler).

    This function is called by the scheduler and runs the backup
    in the current thread since boto3 is synchronous.
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
    """Start the background scheduler."""
    global scheduler

    if not config.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled")
        return

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    scheduler = AsyncIOScheduler()

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
    weather_msg = ", weather prefetch at :55" if config.WEATHER_ENABLED else ""
    logger.info(
        f"Scheduler started - hourly generation at :00{weather_msg}, "
        "API calls flush every minute, version refresh at 00:05"
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
    Run initial image generation on startup.

    Uses static data from JSON files - no API calls needed.
    Also refreshes version info from GitHub API.
    """
    logger.info("Running initial generation from static data")

    if config.WEATHER_ENABLED:
        try:
            await prefetch_weather()
            logger.info("Weather prefetched on startup")
        except Exception as e:
            logger.error("Error prefetching weather on startup: %s", e, exc_info=True)

    try:
        await collect_and_generate()
    except Exception as e:
        logger.error(f"Error in initial generation: {e}", exc_info=True)

    try:
        await refresh_version_info()
        logger.info("Version info refreshed on startup")
    except Exception as e:
        logger.error(f"Error refreshing version info on startup: {e}", exc_info=True)


# Legacy function names for backwards compatibility
sync_full_season = collect_and_generate
sync_season_to_db = collect_and_generate
