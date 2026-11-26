"""Offline context for the optional teams dashboard panel."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from zoneinfo import ZoneInfo

from app.config import config
from app.models import Race
from app.services.circuit_metadata import COUNTRY_MAP, canonical_circuit_id
from app.services.f1_service import F1Service
from app.services.renderer_assets import get_country_flag_iso_code
from app.services.weather_service import (
    WeatherData,
    WeatherService,
    _race_weather_cache_key,
    get_cached_circuit_weather,
)
from app.utils.race_times import local_session_start, parse_session_start, session_over
from app.utils.timezones import normalize_timezone


class TeamsExtra(StrEnum):
    """Available optional panel contents."""

    NONE = "none"
    COUNTDOWN = "countdown"
    FLAGS = "flags"
    TRACK = "track"
    WEATHER = "weather"

    @classmethod
    def _missing_(cls, value: object) -> "TeamsExtra | None":
        """Keep previously copied timeline/combo URLs on the consolidated countdown."""
        if value in ("timeline", "combo"):
            return cls.COUNTDOWN
        return None


@dataclass(frozen=True)
class RoundInfo:
    """One non-cancelled championship round."""

    round: int
    code: str
    iso2: str | None
    date: date
    done: bool


@dataclass(frozen=True)
class TeamsExtraContext:
    """A local, network-free snapshot used by panel rendering."""

    season: int
    rounds: tuple[RoundInfo, ...]
    next_round: int
    total_rounds: int
    race_name: str
    circuit_id: str
    circuit_name: str
    start_local: datetime
    days_left: int
    weather: WeatherData | None
    timezone_name: str = config.DEFAULT_TIMEZONE
    # Preserve the legacy day token when the weather panel has nothing to draw.
    default_today: date | None = None
    # False for a race whose start hour is not published yet (date-only schedule entry).
    start_time_known: bool = True


_CODES = dict(
    zip(
        "albert_park shanghai suzuka bahrain jeddah miami villeneuve monaco catalunya "
        "red_bull_ring silverstone spa hungaroring zandvoort monza madring baku sepang "
        "marina_bay americas rodriguez interlagos las_vegas losail yas_marina imola".split(),
        "AUS CHN JPN BHR KSA MIA CAN MON BAR AUT GBR BEL HUN NED ITA MAD AZE MAL SIN "
        "USA MEX BRA LVG QAT ABU IMO".split(),
        strict=True,
    )
)


def _start(race: Race) -> datetime:
    """Return the race start as aware UTC for ordering; a TBA race sorts at UTC midnight.

    Only for ordering; use _local_start/_race_over for anything shown or compared to now.
    """
    return parse_session_start(race.date, race.time) or datetime.combine(
        date.fromisoformat(race.date), datetime.min.time(), UTC
    )


def _local_start(race: Race, zone: ZoneInfo) -> datetime:
    """Return the start in the viewer's zone; a date-only race keeps its calendar date."""
    return local_session_start(race.date, race.time, zone)


def _race_over(race: Race, zone: ZoneInfo, now: datetime) -> bool:
    """A timed race is over once it starts; a date-only race after its local day ends."""
    return session_over(race.date, race.time, zone, now)


def build_teams_extra_context(
    extra: TeamsExtra,
    season: int,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> TeamsExtraContext | None:
    """Build a panel snapshot from bundled seasons and existing memory caches only."""
    if extra == TeamsExtra.NONE:
        return None
    now = now or datetime.now(UTC)
    timezone_name = normalize_timezone(timezone_name or config.DEFAULT_TIMEZONE)
    zone = ZoneInfo(timezone_name)
    local_today = now.astimezone(zone).date()
    races = sorted((r for r in F1Service.get_season_from_static(season) if r.round), key=_start)
    if not races:
        return None
    candidates = list(races)
    for year in {local_today.year, local_today.year + 1} - {season}:
        candidates.extend(r for r in F1Service.get_season_from_static(year) if r.round)
    upcoming = next(
        (r for r in sorted(candidates, key=_start) if not _race_over(r, zone, now)), None
    )
    if upcoming is None or int(upcoming.season) != season or season < local_today.year:
        return None
    circuit = upcoming.Circuit
    circuit_id = canonical_circuit_id(circuit.circuitId)
    start = _start(upcoming)
    start_local = _local_start(upcoming, zone)
    weather = None
    if extra == TeamsExtra.WEATHER:
        location = circuit.Location
        if location.lat is not None and location.long is not None:
            weather = WeatherService.get_cached_value(
                _race_weather_cache_key(float(location.lat), float(location.long), start)
            )
        weather = weather or get_cached_circuit_weather(circuit_id)
    rounds = tuple(
        RoundInfo(
            int(r.round or 0),
            _CODES.get(
                canonical_circuit_id(r.Circuit.circuitId), r.Circuit.Location.country[:3].upper()
            ),
            get_country_flag_iso_code(r.Circuit.Location.country, COUNTRY_MAP) or None,
            _local_start(r, zone).date(),
            _race_over(r, zone, now),
        )
        for r in races
    )
    return TeamsExtraContext(
        season,
        rounds,
        int(upcoming.round or 0),
        len(rounds),
        upcoming.raceName.replace("Grand Prix", "GP"),
        circuit_id,
        circuit.circuitName,
        start_local,
        max(0, (start_local.date() - local_today).days),
        weather,
        timezone_name,
        now.astimezone(ZoneInfo(config.DEFAULT_TIMEZONE)).date(),
        start_time_known=bool(upcoming.time),
    )


def teams_extra_cache_token(extra: TeamsExtra, ctx: TeamsExtraContext | None) -> str:
    """Identify the local day, next round, and displayed cached weather values."""
    if extra == TeamsExtra.NONE or ctx is None:
        return ""
    # Derive the snapshot's local today, keeping injected clocks deterministic.
    today = ctx.start_local.date() - timedelta(days=ctx.days_left)
    if extra == TeamsExtra.WEATHER and ctx.weather is None and ctx.default_today is not None:
        today = ctx.default_today
    token = f"_x{extra}_{today:%Y%m%d}_r{ctx.next_round}"
    if extra == TeamsExtra.WEATHER and ctx.weather is not None:
        wx = ctx.weather
        values = (round(wx.temperature_c), wx.weather_code, wx.precip_display)
        token += "_" + sha256(repr(values).encode()).hexdigest()[:10]
    if normalize_timezone(ctx.timezone_name) != normalize_timezone(config.DEFAULT_TIMEZONE) and (
        extra != TeamsExtra.WEATHER or ctx.weather is not None
    ):
        token += f"_{normalize_timezone(ctx.timezone_name).replace('/', '_')}"
    return token
