"""Scheduler service for hourly image generation using static data."""

import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import aiofiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from PIL import Image

from app.config import LANGUAGE_CODES, config
from app.services.bwr_renderer import BwrRenderer
from app.services.bwry_renderer import BwryRenderer
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.image_keys import get_calendar_image_key, get_teams_image_key
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.version_service import refresh_version_info
from app.services.weather_service import (
    WeatherData,
    WeatherService,
    get_weather_context,
    load_circuit_weather_to_cache,
    prefetch_weather_for_next_race,
    set_cached_circuit_weather,
)
from app.state import clear_bmp_cache, get_and_clear_api_calls_buffer
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
    img_file = Image.open(BytesIO(bmp_data))

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

    for lang in SUPPORTED_LANGUAGES:
        translator = get_translator(lang)

        # Calendar preview - generate variants for different weather types and displays
        if race_data:
            # Display variants: 1bit, spectra6, black/white/red, and black/white/red/yellow
            display_variants = [
                ("1bit", Renderer(translator, lang)),
                ("spectra6", Spectra6Renderer(translator, lang)),
                ("bwr", BwrRenderer(translator, lang)),
                ("bwry", BwryRenderer(translator, lang)),
            ]

            for display_name, display_renderer in display_variants:
                for weather_type, wd in weather_variants:
                    try:
                        bmp_data = display_renderer.render_calendar(
                            race_data, historical_data, wd, weather_type
                        )

                        # Build filename suffix
                        suffix = f"_{lang}"
                        if display_name == "spectra6":
                            suffix += "_spectra6"
                        elif display_name == "bwr":
                            suffix += "_bwr"
                        elif display_name == "bwry":
                            suffix += "_bwry"
                        if weather_type != "off":
                            suffix += f"_weather_{weather_type}"

                        is_color = display_name in {"spectra6", "bwr", "bwry"}

                        # Homepage preview (small, only for default 1bit+off)
                        if display_name == "1bit" and weather_type == "off":
                            homepage_png = _bmp_to_png(bmp_data, width=400)
                            homepage_path = images_dir / f"preview_calendar_{lang}.png"
                            async with aiofiles.open(homepage_path, "wb") as f:
                                await f.write(homepage_png)

                        # Configure preview (full size, all variants)
                        configure_png = _bmp_to_png(
                            bmp_data, full_size=True, preserve_color=is_color
                        )
                        configure_path = images_dir / f"configure_calendar{suffix}.png"
                        async with aiofiles.open(configure_path, "wb") as f:
                            await f.write(configure_png)

                        logger.debug("Generated configure preview: %s", configure_path)
                    except Exception as e:
                        logger.error(
                            "Error generating calendar preview (%s, %s, %s): %s",
                            lang,
                            display_name,
                            weather_type,
                            e,
                        )

            logger.info("Generated calendar previews for %s", lang)

        # Teams preview
        try:
            if teams_data is None or not teams_data.teams:
                logger.warning(
                    "Skipping teams previews for %s: no teams data for %d", lang, teams_year
                )
                continue
            display_variants = [
                ("1bit", Renderer(translator, lang)),
                ("spectra6", Spectra6Renderer(translator, lang)),
                ("bwr", BwrRenderer(translator, lang)),
                ("bwry", BwryRenderer(translator, lang)),
            ]

            homepage_path = images_dir / f"preview_teams_{lang}.png"
            configure_paths: list[Path] = []

            for display_name, display_renderer in display_variants:
                bmp_data = display_renderer.render_teams_drivers(teams_data)
                is_color = display_name in {"spectra6", "bwr", "bwry"}

                if display_name == "1bit":
                    homepage_png = _bmp_to_png(bmp_data, width=400)
                    async with aiofiles.open(homepage_path, "wb") as f:
                        await f.write(homepage_png)

                suffix = f"_{lang}"
                if display_name != "1bit":
                    suffix += f"_{display_name}"

                configure_png = _bmp_to_png(
                    bmp_data,
                    full_size=True,
                    preserve_color=is_color,
                )
                configure_path = images_dir / f"configure_teams{suffix}.png"
                async with aiofiles.open(configure_path, "wb") as f:
                    await f.write(configure_png)
                configure_paths.append(configure_path)

            logger.info("Generated teams previews: %s, %s", homepage_path, configure_paths)
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
) -> bool:
    """Render and save a single pregenerated calendar variant."""
    translator = get_translator(lang)
    if display == "spectra6":
        renderer = Spectra6Renderer(translator, lang)
    elif display == "bwr":
        renderer = BwrRenderer(translator, lang)
    elif display == "bwry":
        renderer = BwryRenderer(translator, lang)
    else:
        renderer = Renderer(translator, lang)

    wd = weather_data if weather_type != "off" else None
    bmp_data = renderer.render_calendar(race_data, historical_data, wd, weather_type)

    image_key = _get_image_key(lang, tz, display, weather_type)
    image_path = images_dir / f"{image_key}.bmp"

    async with aiofiles.open(image_path, "wb") as f:
        await f.write(bmp_data)

    await db.save_generated_image(image_key=image_key, image_path=str(image_path), lang=lang)
    return True


def _delete_existing_bmps(images_dir: Path) -> int:
    """Delete previously generated BMP files before a fresh generation run."""
    deleted_count = 0
    for bmp_file in images_dir.glob("*.bmp"):
        bmp_file.unlink()
        deleted_count += 1
    return deleted_count


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
) -> int:
    """Generate the base language/display/weather combinations."""
    generated_count = 0

    for lang in SUPPORTED_LANGUAGES:
        for display in display_types:
            for weather_type, wd in weather_by_type.items():
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

    return generated_count


async def _generate_popular_tz_variants(
    *,
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    display_types: list[str],
    weather_by_type: dict[str, WeatherData | None],
) -> int:
    """Generate extra calendar variants for the most-used non-default timezones."""
    generated_count = 0

    popular_variants = await db.get_popular_tz_variants(
        min_requests=10, hours=24, limit=20, exclude_tz=config.DEFAULT_TIMEZONE
    )
    if not popular_variants:
        return 0

    logger.info("Generating %d popular TZ variants", len(popular_variants))

    for variant in popular_variants:
        lang = variant["lang"]
        tz = variant["tz"]
        if lang not in SUPPORTED_LANGUAGES:
            logger.debug("Skipping unsupported language: %s", lang)
            continue

        race_data_converted = convert_race_times_to_timezone(race_data, tz)

        for display in display_types:
            for weather_type, wd in weather_by_type.items():
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

    return generated_count


async def _generate_teams_bmp_variants(
    *,
    images_dir: Path,
    db: Database,
) -> int:
    """Generate pregenerated teams BMPs for all languages and display modes."""
    from app.services.teams_service import TeamsService, get_default_teams_year

    teams_year = get_default_teams_year()
    teams_service = TeamsService()
    try:
        teams_data = await teams_service.get_teams_and_drivers(teams_year)
    except Exception as exc:
        logger.error("Error fetching teams BMP data for %d: %s", teams_year, exc, exc_info=True)
        return 0

    if not teams_data.teams:
        logger.warning("Skipping teams BMP generation: no teams data for %d", teams_year)
        return 0

    generated_count = 0
    display_variants = ["1bit", "spectra6", "bwr", "bwry"]

    for lang in SUPPORTED_LANGUAGES:
        translator = get_translator(lang)
        for display in display_variants:
            try:
                if display == "spectra6":
                    renderer = Spectra6Renderer(translator, lang)
                elif display == "bwr":
                    renderer = BwrRenderer(translator, lang)
                elif display == "bwry":
                    renderer = BwryRenderer(translator, lang)
                else:
                    renderer = Renderer(translator, lang)

                bmp_data = renderer.render_teams_drivers(teams_data)
                suffix = ""
                if display == "spectra6":
                    suffix = "_spectra6"
                elif display == "bwr":
                    suffix = "_bwr"
                elif display == "bwry":
                    suffix = "_bwry"

                image_key = get_teams_image_key(lang, teams_year, display=display)
                image_path = images_dir / f"{image_key}.bmp"

                async with aiofiles.open(image_path, "wb") as f:
                    await f.write(bmp_data)

                await db.save_generated_image(
                    image_key=image_key,
                    image_path=str(image_path),
                    lang=lang,
                    season=teams_year,
                )
                generated_count += 1
            except Exception as exc:
                logger.error(
                    "Error generating teams BMP (%s, %s, %d): %s",
                    lang,
                    display,
                    teams_year,
                    exc,
                    exc_info=True,
                )

    logger.info(
        "Generated teams BMP variants for %d languages x %d displays (%d total)",
        len(SUPPORTED_LANGUAGES),
        len(display_variants),
        generated_count,
    )
    return generated_count


async def collect_and_generate() -> None:
    """Generate pregenerated calendar and teams BMP variants from static data."""
    logger.info("Starting image generation from static data")

    try:
        db = Database()
        f1_service = F1Service()

        images_dir = Path(config.IMAGES_PATH)
        images_dir.mkdir(parents=True, exist_ok=True)

        deleted_count = _delete_existing_bmps(images_dir)
        if deleted_count > 0:
            logger.info("Deleted %d existing BMP files", deleted_count)

        race_data = f1_service.get_next_race_from_static()
        if not race_data:
            logger.warning("No upcoming race found in static data")
            return

        logger.info("Next race: %s (from static data)", race_data.get("race_name"))

        historical_data = _load_historical_data(race_data)

        _, _, weather_by_type = await _load_weather_context(race_data)

        display_types = ["1bit", "spectra6", "bwr", "bwry"]
        logger.info(
            "Generating variants: displays=%s, weather=%s",
            display_types,
            list(weather_by_type.keys()),
        )

        generated_count = 0
        generated_count += await _generate_base_variants(
            images_dir=images_dir,
            db=db,
            race_data=race_data,
            historical_data=historical_data,
            display_types=display_types,
            weather_by_type=weather_by_type,
        )
        generated_count += await _generate_popular_tz_variants(
            images_dir=images_dir,
            db=db,
            race_data=race_data,
            historical_data=historical_data,
            display_types=display_types,
            weather_by_type=weather_by_type,
        )

        await db.set_cache_meta("last_generation", datetime.now(timezone.utc).isoformat())
        clear_bmp_cache()

        await db.cleanup_old_stats(days=30)
        await generate_preview_pngs(race_data, historical_data)
        generated_count += await _generate_teams_bmp_variants(images_dir=images_dir, db=db)

        logger.info("Image generation completed: %d images", generated_count)

    except Exception as exc:
        logger.error("Error in image generation: %s", exc, exc_info=True)


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
    enabled), backup (if configured), version refresh (00:05 daily).
    Returns if scheduler disabled or already running.
    """
    global scheduler  # skipcq: PYL-W0603 - singleton pattern for scheduler instance

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
    """Stop the background scheduler."""
    global scheduler  # skipcq: PYL-W0603 - singleton pattern for scheduler instance

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
