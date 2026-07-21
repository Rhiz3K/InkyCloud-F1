"""Scheduled BMP generation and preview orchestration."""

from __future__ import annotations

import asyncio
import functools
import logging
import weakref
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from app.config import LANGUAGE_CODES, config
from app.services.database import Database, get_database
from app.services.f1_service import F1Service
from app.services.generation_freshness import GENERATION_SUCCESS_META_KEY
from app.services.i18n import get_translator
from app.services.image_keys import (
    get_calendar_image_key,
    get_configure_preview_filename,
    get_preview_filename,
    get_teams_image_key,
)
from app.services.renderers import COLOR_DISPLAYS, DISPLAY_TYPES, create_renderer
from app.services.weather_service import WeatherData, _parse_coordinate, get_weather_context
from app.state import clear_bmp_cache
from app.utils.async_locks import LoopLockRegistry, get_loop_lock
from app.utils.async_tasks import RENDER_WORKER_COUNT, run_render
from app.utils.atomic_io import atomic_write_bytes as _atomic_write_bytes
from app.utils.etag import encode_etag_sidecar, etag_sidecar_path, strong_etag
from app.utils.image_conversion import bmp_to_png
from app.utils.race_times import convert_race_times_to_timezone

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = list(LANGUAGE_CODES)
_generation_locks: LoopLockRegistry = weakref.WeakKeyDictionary()
_T = TypeVar("_T")


async def _bounded_gather(jobs: Sequence[Callable[[], Awaitable[_T]]]) -> list[_T]:
    """Run async jobs concurrently without exceeding the render worker pool."""
    semaphore = asyncio.Semaphore(RENDER_WORKER_COUNT)

    async def run_one(job: Callable[[], Awaitable[_T]]) -> _T:
        """Run one render job while holding a worker-pool permit."""
        async with semaphore:
            return await job()

    return list(await asyncio.gather(*(run_one(job) for job in jobs)))


async def _write_bmp_artifact(image_path: Path, bmp_data: bytes) -> str:
    """Atomically write a BMP and its mtime-bound strong-ETag sidecar."""
    await _atomic_write_bytes(image_path, bmp_data)
    image_stat = await asyncio.to_thread(image_path.stat)
    etag = strong_etag(bmp_data)
    await _atomic_write_bytes(
        etag_sidecar_path(image_path),
        encode_etag_sidecar(image_stat.st_mtime_ns, etag),
    )
    return etag


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


def _calendar_preview_pngs_from_bmp(
    bmp_path: Path,
    lang: str,
    display_name: str,
    weather_type: str,
) -> list[tuple[str, bytes]]:
    """Convert one pregenerated calendar BMP into its web-preview PNGs."""
    is_color = display_name in COLOR_DISPLAYS
    bmp_data = bmp_path.read_bytes()

    outputs: list[tuple[str, bytes]] = []
    if display_name == "1bit" and weather_type == "off":
        outputs.append((get_preview_filename("calendar", lang), bmp_to_png(bmp_data, width=400)))

    outputs.append(
        (
            get_configure_preview_filename(
                "calendar", lang, display=display_name, weather=weather_type
            ),
            bmp_to_png(bmp_data, full_size=True, preserve_color=is_color),
        )
    )
    return outputs


def _teams_preview_pngs_from_bmp(
    bmp_path: Path, lang: str, display_name: str
) -> list[tuple[str, bytes]]:
    """Convert one pregenerated teams BMP into its web-preview PNGs."""
    is_color = display_name in COLOR_DISPLAYS
    bmp_data = bmp_path.read_bytes()

    outputs: list[tuple[str, bytes]] = []
    if display_name == "1bit":
        outputs.append((get_preview_filename("teams", lang), bmp_to_png(bmp_data, width=400)))

    outputs.append(
        (
            get_configure_preview_filename("teams", lang, display=display_name),
            bmp_to_png(bmp_data, full_size=True, preserve_color=is_color),
        )
    )
    return outputs


async def generate_preview_pngs(weather_types: list[str], teams_year: int) -> None:
    """
    Generate PNG preview images for landing page.

    Creates small PNG previews (400x240) for each screen type and language.
    These are used on the landing page for screen type selection.

    Preview generation converts BMPs created earlier in the same scheduler run. It does not
    invoke the expensive renderers or fetch upstream data a second time.
    """
    images_dir = Path(config.IMAGES_PATH)
    images_dir.mkdir(parents=True, exist_ok=True)
    display_types = DISPLAY_TYPES

    for lang in SUPPORTED_LANGUAGES:
        calendar_written = 0
        for display_name in display_types:
            for weather_type in weather_types:
                image_key = _get_image_key(lang, display=display_name, weather=weather_type)
                bmp_path = images_dir / f"{image_key}.bmp"
                if not bmp_path.is_file():
                    logger.debug("Skipping missing calendar preview source: %s", bmp_path)
                    continue
                try:
                    outputs = await run_render(
                        functools.partial(
                            _calendar_preview_pngs_from_bmp,
                            bmp_path,
                            lang,
                            display_name,
                            weather_type,
                        )
                    )
                    for filename, png_data in outputs:
                        await _atomic_write_bytes(images_dir / filename, png_data)
                        calendar_written += 1
                except Exception as e:
                    logger.error(
                        "Error converting calendar preview (%s, %s, %s): %s",
                        lang,
                        display_name,
                        weather_type,
                        e,
                    )
        logger.info("Generated %d calendar preview PNGs for %s", calendar_written, lang)

        teams_written = 0
        for display_name in display_types:
            image_key = get_teams_image_key(lang, teams_year, display=display_name)
            bmp_path = images_dir / f"{image_key}.bmp"
            if not bmp_path.is_file():
                logger.debug("Skipping missing teams preview source: %s", bmp_path)
                continue
            try:
                outputs = await run_render(
                    functools.partial(_teams_preview_pngs_from_bmp, bmp_path, lang, display_name)
                )
                for filename, png_data in outputs:
                    await _atomic_write_bytes(images_dir / filename, png_data)
                    teams_written += 1
            except Exception as e:
                logger.error(
                    "Error converting teams preview (%s, %s, %d): %s",
                    lang,
                    display_name,
                    teams_year,
                    e,
                )
        logger.info("Generated %d teams preview PNGs for %s", teams_written, lang)


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
        await _write_bmp_artifact(image_path, bmp_data)
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
            etag_sidecar_path(bmp_file).unlink(missing_ok=True)
            removed += 1
    for sidecar in images_dir.glob("*.bmp.etag"):
        if not sidecar.with_suffix("").exists():
            sidecar.unlink()
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


def _has_weather_coordinates(race_data: dict) -> bool:
    """Return whether race data contains valid latitude and longitude values."""
    circuit = race_data.get("circuit", {})
    displayed_lon = circuit.get("long") if circuit.get("long") is not None else circuit.get("lon")
    lat = _parse_coordinate(circuit.get("lat"))
    lon = _parse_coordinate(displayed_lon)
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def _parse_race_datetime_utc(race_data: dict) -> datetime | None:
    """Extract the race session timestamp as an aware UTC datetime."""
    schedule = race_data.get("schedule", [])
    race_session = next(
        (session for session in schedule if str(session.get("name", "")).lower() == "race"),
        None,
    )
    if not race_session:
        return None

    dt_str = race_session.get("datetime")
    if not dt_str:
        return None

    try:
        race_dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
    except ValueError:
        logger.debug("Invalid race datetime for weather degradation check: %s", dt_str)
        return None

    if race_dt.tzinfo is None:
        return race_dt.replace(tzinfo=timezone.utc)
    return race_dt.astimezone(timezone.utc)


def _race_weather_expected(race_data: dict) -> bool:
    """Return whether historical or forecast race weather should be available."""
    race_dt = _parse_race_datetime_utc(race_data)
    if race_dt is None:
        return False

    now = datetime.now(timezone.utc)
    if race_dt < now:
        return True

    # WeatherService can fetch up to 16 forecast days, including today.
    forecast_days = (race_dt.date() - now.date()).days + 1
    return forecast_days <= 16


def _weather_context_degraded(
    race_data: dict,
    weather_by_type: dict[str, WeatherData | None],
) -> bool:
    """Return whether any weather source expected for rendering is unavailable."""
    if not config.WEATHER_ENABLED or not _has_weather_coordinates(race_data):
        return False

    expected = {"current"}
    if _race_weather_expected(race_data):
        expected.add("race")

    return any(weather_by_type.get(weather_type) is None for weather_type in expected)


async def _generate_base_variants(
    *,
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    display_types: Sequence[str],
    weather_by_type: dict[str, WeatherData | None],
) -> tuple[set[Path], int]:
    """Generate the base language/display/weather combinations.

    Returns (written paths, failure count).
    """
    jobs = [
        functools.partial(
            _generate_variant,
            images_dir,
            db,
            race_data,
            historical_data,
            weather_data,
            lang,
            None,
            display,
            weather_type,
        )
        for lang in SUPPORTED_LANGUAGES
        for display in display_types
        for weather_type, weather_data in weather_by_type.items()
    ]
    results = await _bounded_gather(jobs)
    return {path for path in results if path is not None}, results.count(None)


async def _generate_popular_tz_variants(
    *,
    images_dir: Path,
    db: Database,
    race_data: dict,
    historical_data,
    display_types: Sequence[str],
    weather_by_type: dict[str, WeatherData | None],
) -> tuple[set[Path], int]:
    """Generate extra calendar variants for the most-used non-default timezones.

    Returns (written paths, failure count).
    """
    failures = 0

    popular_variants = await db.get_popular_tz_variants(
        min_requests=10, hours=24, limit=20, exclude_tz=config.DEFAULT_TIMEZONE
    )
    if not popular_variants:
        return set(), failures

    logger.info("Generating %d popular TZ variants", len(popular_variants))

    jobs: list[Callable[[], Awaitable[Path | None]]] = []
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

        jobs.extend(
            functools.partial(
                _generate_variant,
                images_dir,
                db,
                race_data_converted,
                historical_data,
                weather_data,
                lang,
                tz,
                display,
                weather_type,
            )
            for display in display_types
            for weather_type, weather_data in weather_by_type.items()
        )

    results = await _bounded_gather(jobs)
    return {path for path in results if path is not None}, failures + results.count(None)


async def _generate_teams_variant(
    *,
    images_dir: Path,
    db: Database,
    teams_data,
    teams_year: int,
    lang: str,
    display: str,
) -> Path | None:
    """Render and persist one teams image without failing sibling variants."""
    try:
        bmp_data = await run_render(
            functools.partial(_render_teams_variant_bytes, lang, display, teams_data)
        )
        image_key = get_teams_image_key(lang, teams_year, display=display)
        image_path = images_dir / f"{image_key}.bmp"
        await _write_bmp_artifact(image_path, bmp_data)
        await db.save_generated_image(
            image_key=image_key,
            image_path=str(image_path),
            lang=lang,
            season=teams_year,
        )
        return image_path
    except Exception as exc:
        logger.error(
            "Error generating teams BMP (%s, %s, %d): %s",
            lang,
            display,
            teams_year,
            exc,
            exc_info=True,
        )
        return None


async def _generate_teams_bmp_variants(
    *,
    images_dir: Path,
    db: Database,
) -> tuple[set[Path], int]:
    """Generate pregenerated teams BMPs for all languages and display modes.

    Returns (written paths, failure count). A failed/empty upstream fetch counts as a failure
    so the caller skips stale pruning and keeps the previous teams BMPs in place.
    """
    from app.services.teams_service import (
        TeamsService,
        get_default_teams_year,
        is_teams_data_cacheable,
    )

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
    if not is_teams_data_cacheable(teams_data):
        logger.warning("Skipping teams BMP generation: standings incomplete for %d", teams_year)
        return set(), 1

    jobs = [
        functools.partial(
            _generate_teams_variant,
            images_dir=images_dir,
            db=db,
            teams_data=teams_data,
            teams_year=teams_year,
            lang=lang,
            display=display,
        )
        for lang in SUPPORTED_LANGUAGES
        for display in DISPLAY_TYPES
    ]
    results = await _bounded_gather(jobs)
    generated = {path for path in results if path is not None}
    failures = results.count(None)

    logger.info(
        "Generated teams BMP variants for %d languages x %d displays (%d total, %d failed)",
        len(SUPPORTED_LANGUAGES),
        len(DISPLAY_TYPES),
        len(generated),
        failures,
    )
    return generated, failures


async def collect_and_generate() -> None:
    """Generate pregenerated calendar and teams BMP variants from static data."""
    async with get_loop_lock(_generation_locks):
        await _collect_and_generate_unlocked()


async def _collect_and_generate_unlocked() -> None:
    """Generate pregenerated calendar and teams BMP variants without acquiring the lock."""
    logger.info("Starting image generation from static data")

    try:
        db = get_database()
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

        # When an expected weather variant is missing (open-meteo outage or partial
        # failure), it simply isn't produced this run. Treat that as degraded so stale
        # pruning doesn't delete previously-good *_weather_* files.
        weather_degraded = _weather_context_degraded(race_data, weather_by_type)

        display_types = DISPLAY_TYPES
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

        teams_paths, teams_failures = await _generate_teams_bmp_variants(
            images_dir=images_dir, db=db
        )
        generated_paths |= teams_paths
        total_failures += teams_failures

        from app.services.teams_service import get_default_teams_year

        await generate_preview_pngs(list(weather_by_type), get_default_teams_year())

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

        if generated_paths and total_failures == 0 and not weather_degraded:
            await db.set_cache_meta(
                GENERATION_SUCCESS_META_KEY, datetime.now(timezone.utc).isoformat()
            )
        clear_bmp_cache()

        if config.STATS_RETENTION_DAYS > 0:
            await db.cleanup_old_stats(days=config.STATS_RETENTION_DAYS)

        logger.info(
            "Image generation completed: %d images (%d failures)",
            len(generated_paths),
            total_failures,
        )
    except Exception as exc:
        logger.error("Error in image generation: %s", exc, exc_info=True)
