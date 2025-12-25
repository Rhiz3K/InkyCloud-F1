"""Scheduler service for hourly image generation using static data."""

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import config
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import Renderer

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
        try:
            from app.main import clear_bmp_cache

            clear_bmp_cache()
        except ImportError:
            pass  # Cache not available (e.g., during tests)

        # Cleanup old hourly stats (keep 30 days) - legacy table
        await db.cleanup_old_stats(days=30)

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

    scheduler.start()
    logger.info("Scheduler started - hourly generation at :00, API calls flush every minute")


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
    """
    logger.info("Running initial generation from static data")

    try:
        await collect_and_generate()
    except Exception as e:
        logger.error(f"Error in initial generation: {e}", exc_info=True)


# Legacy function names for backwards compatibility
sync_full_season = collect_and_generate
sync_season_to_db = collect_and_generate
