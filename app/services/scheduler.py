"""APScheduler wiring for periodic application jobs."""

from __future__ import annotations

import logging
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import config
from app.services.scheduler_generation import collect_and_generate as _collect_and_generate
from app.services.scheduler_maintenance import (
    _historical_refresh_is_due,
)
from app.services.scheduler_maintenance import (
    flush_api_calls_to_db as _flush_api_calls_to_db,
)
from app.services.scheduler_maintenance import (
    refresh_historical_results as _refresh_historical_results,
)
from app.services.scheduler_weather import (
    fetch_all_circuits_weather as _fetch_all_circuits_weather,
)
from app.services.scheduler_weather import (
    load_weather_from_db as _load_weather_from_db,
)
from app.services.scheduler_weather import (
    prefetch_weather as _prefetch_weather,
)
from app.services.version_service import refresh_version_info

logger = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None


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
        raise ValueError("cron expression must contain five fields")

    cron_kwargs = {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": _normalize_cron_day_of_week(parts[4]),
    }
    CronTrigger(**cron_kwargs, timezone=timezone.utc)
    return cron_kwargs


def _normalize_cron_day_of_week(value: str) -> str:
    """Expand standard-cron weekdays into unambiguous APScheduler day names.

    Standard cron numbers are Sunday-first (0/7 = Sunday), while APScheduler's
    numeric values are Monday-first.  Expanding ranges also handles valid
    wrap-around expressions such as ``5-0`` without emitting an inverted
    APScheduler range.
    """
    weekday_names = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    weekday_numbers = {name: index for index, name in enumerate(weekday_names)}
    full_weekday_numbers = {
        "sunday": 0,
        "monday": 1,
        "tuesday": 2,
        "wednesday": 3,
        "thursday": 4,
        "friday": 5,
        "saturday": 6,
    }

    def parse_weekday(token: str) -> int:
        """Return a Sunday-first weekday number for one numeric or named token."""
        normalized = token.strip().lower()
        if normalized.isdigit():
            number = int(normalized)
            if number == 7:
                return 0
            if 0 <= number <= 6:
                return number
        elif normalized in weekday_numbers:
            return weekday_numbers[normalized]
        elif normalized in full_weekday_numbers:
            return full_weekday_numbers[normalized]
        raise ValueError

    def inclusive_range(start: int, end: int) -> list[int]:
        """Expand an inclusive weekday range, wrapping through Sunday when needed."""
        values = [start]
        while values[-1] != end:
            values.append((values[-1] + 1) % 7)
        return values

    def expand_part(part: str) -> list[int]:
        """Expand one comma-delimited cron weekday expression."""
        if not part or part.count("/") > 1:
            raise ValueError
        base, separator, step_text = part.partition("/")
        step = 1
        if separator:
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError
            step = int(step_text)

        if base == "*":
            values = list(range(7))
        elif base.count("-") == 1:
            start_text, end_text = base.split("-", 1)
            values = inclusive_range(parse_weekday(start_text), parse_weekday(end_text))
        elif "-" not in base:
            start = parse_weekday(base)
            values = inclusive_range(start, 6) if separator else [start]
        else:
            raise ValueError
        return values[::step]

    try:
        expanded: list[int] = []
        for expression in value.split(","):
            for weekday in expand_part(expression.strip()):
                if weekday not in expanded:
                    expanded.append(weekday)
        return ",".join(weekday_names[weekday] for weekday in expanded)
    except ValueError as exc:
        raise ValueError(f"invalid day-of-week field: {value}") from exc


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

    try:
        cron_kwargs = _parse_cron_expression(config.BACKUP_CRON)
        trigger = CronTrigger(**cron_kwargs, timezone=timezone.utc)
    except ValueError as exc:
        logger.critical("Invalid BACKUP_CRON=%r; refusing to start: %s", config.BACKUP_CRON, exc)
        raise ValueError(f"Invalid BACKUP_CRON={config.BACKUP_CRON!r}") from exc

    sched.add_job(
        _run_backup,
        trigger=trigger,
        id="s3_backup",
        name=f"S3 database backup (cron: {config.BACKUP_CRON})",
        replace_existing=True,
    )

    logger.info("S3 backup job registered (cron: %s)", config.BACKUP_CRON)


def start_scheduler() -> None:
    """
    Initialize and start the background scheduler with all jobs.

    Jobs: hourly image gen (:00), API flush (every min), weather (:55 if
    enabled), historical refresh (daily at 06:10 UTC), backup (if configured),
    version refresh (hourly at :05).
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
            _prefetch_weather,
            trigger=CronTrigger(minute=55, timezone=timezone.utc),
            id="weather_prefetch",
            name="Weather data prefetch for next race",
            replace_existing=True,
        )

    # Hourly: Regenerate images from static data
    scheduler.add_job(
        _collect_and_generate,
        trigger=CronTrigger(minute=0, timezone=timezone.utc),
        id="hourly_generation",
        name="Hourly image generation from static data",
        replace_existing=True,
    )

    # Daily: Refresh historical results from Jolpica and regenerate images if changed.
    scheduler.add_job(
        _refresh_historical_results,
        trigger=CronTrigger(hour=6, minute=10, timezone=timezone.utc),
        id="historical_results_refresh",
        name="Daily historical results refresh from Jolpica",
        replace_existing=True,
    )

    # Every minute: Flush API calls buffer to database
    scheduler.add_job(
        _flush_api_calls_to_db,
        trigger=CronTrigger(second=0, timezone=timezone.utc),
        id="flush_api_calls",
        name="Flush API calls to database",
        replace_existing=True,
    )

    # Hourly at :55: Fetch weather for all circuits (before image generation at :00)
    if config.WEATHER_ENABLED:
        scheduler.add_job(
            _fetch_all_circuits_weather,
            trigger=CronTrigger(minute=55, timezone=timezone.utc),
            id="fetch_circuit_weather",
            name="Fetch weather for all circuits",
            replace_existing=True,
        )

    # Conditional: S3 database backup
    _register_backup_job(scheduler)

    # Hourly at :05: Refresh version info from GitHub API
    scheduler.add_job(
        refresh_version_info,
        trigger=CronTrigger(minute=5, timezone=timezone.utc),
        id="refresh_version_info",
        name="Refresh version info from GitHub (hourly)",
        replace_existing=True,
    )

    scheduler.start()
    weather_info = ", weather at :55" if config.WEATHER_ENABLED else ""
    logger.info(
        "Scheduler started - generation at :00%s, historical refresh daily at 06:10 UTC, "
        "API flush every min, version at :05",
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
    Perform startup: warm weather from SQLite, generate images, then refresh upstream data.

    Image generation runs before the historical catch-up and the all-circuit weather fetch:
    both can take minutes (or the full 90-minute refresh budget while Jolpica rate-limits),
    and readiness depends only on a fresh core calendar generation. The hourly job publishes
    whatever those later refreshes change. Failures in individual steps are logged but don't
    stop subsequent steps.
    """
    logger.info("Running initial generation from static data")

    if config.WEATHER_ENABLED:
        try:
            await _load_weather_from_db()
        except Exception as e:
            logger.warning("Error loading weather from database: %s", e)

    try:
        await _collect_and_generate()
    except Exception as e:
        logger.error("Error in initial generation: %s", e, exc_info=True)

    if config.SCHEDULER_ENABLED:
        try:
            if await _historical_refresh_is_due():
                logger.info("Historical refresh is stale or missing; running startup catch-up")
                await _refresh_historical_results()
        except Exception as e:
            logger.error("Error in startup historical refresh: %s", e, exc_info=True)

    if config.WEATHER_ENABLED:
        try:
            await _fetch_all_circuits_weather()
        except Exception as e:
            logger.error("Error fetching initial weather: %s", e, exc_info=True)

    # Refresh version info
    try:
        await refresh_version_info()
        logger.info("Version info refreshed on startup")
    except Exception as e:
        logger.error("Error refreshing version info on startup: %s", e, exc_info=True)
