"""Data models for F1 E-Ink calendar service."""

import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# Historical Results Models (for previous year's race data)
# ============================================================================


class DriverInfo(BaseModel):
    """Driver information from results."""

    code: str  # e.g., "VER"
    given_name: str  # e.g., "Max"
    family_name: str  # e.g., "Verstappen"

    @property
    def display_name(self) -> str:
        """Short display name for E-Ink (e.g., 'Verstappen')."""
        return self.family_name


class ConstructorInfo(BaseModel):
    """Constructor/Team information from results."""

    name: str  # e.g., "Red Bull"


class RaceResultEntry(BaseModel):
    """Single race result entry (podium position)."""

    position: int
    driver: DriverInfo
    constructor: ConstructorInfo
    time: Optional[str] = None  # e.g., "1:33:56.736" or "+11.987"


class QualifyingResultEntry(BaseModel):
    """Single qualifying result entry (podium position)."""

    position: int
    driver: DriverInfo
    constructor: ConstructorInfo
    q3_time: Optional[str] = None  # Best Q3 time, e.g., "1:29.708"


class HistoricalData(BaseModel):
    """Container for historical race data (previous year's results)."""

    season: Optional[int] = None  # The year of the historical data (e.g., 2024)
    is_new_track: bool = False  # True if no previous race exists at this circuit
    race_results: list[RaceResultEntry] = []  # Top 3 race finishers
    qualifying_results: list[QualifyingResultEntry] = []  # Top 3 qualifiers


class Location(BaseModel):
    """Location information from Jolpica API."""

    lat: Optional[str] = None
    long: Optional[str] = None
    locality: str
    country: str


class Circuit(BaseModel):
    """Circuit information."""

    circuitId: str
    circuitName: str
    Location: Location
    url: Optional[str] = None


class RaceSession(BaseModel):
    """Race session timing information."""

    date: str
    time: str


class Race(BaseModel):
    """Race information from Jolpica API."""

    season: str
    round: str
    raceName: str
    Circuit: Circuit
    date: str
    time: Optional[str] = None
    FirstPractice: Optional[RaceSession] = None
    SecondPractice: Optional[RaceSession] = None
    ThirdPractice: Optional[RaceSession] = None
    Qualifying: Optional[RaceSession] = None
    Sprint: Optional[RaceSession] = None


class F1Response(BaseModel):
    """Response from Jolpica F1 API."""

    MRData: dict = Field(..., description="Main response data")

    @property
    def race(self) -> Optional[Race]:
        """Extract the next race from the response."""
        try:
            race_table = self.MRData.get("RaceTable", {})
            races = race_table.get("Races", [])
            if races:
                return Race(**races[0])
        except Exception as e:
            logger.error(f"Failed to parse race data: {e}", exc_info=True)
            return None
        return None


class ScheduleEvent(BaseModel):
    """Formatted schedule event for display."""

    name: str
    datetime: datetime
    display_time: str


# ============================================================================
# Championship Standings Models (for driver and constructor standings)
# ============================================================================


class DriverStanding(BaseModel):
    """Driver championship standing entry."""

    position: int
    points: float
    wins: int = 0
    driver_code: str = Field(default="", description="Driver code, e.g. VER")
    driver_name: str = Field(default="", description="Family name for display")
    driver_given_name: str = ""
    nationality: str = ""
    constructor_name: str = ""


class ConstructorStanding(BaseModel):
    """Constructor/team championship standing entry."""

    position: int
    points: float
    wins: int = 0
    constructor_name: str = Field(default="", description="Team name, e.g. Red Bull")
    nationality: str = ""


class StandingsData(BaseModel):
    """Container for championship standings data."""

    season: int
    round: int = Field(default=0, description="After which round these standings apply")
    driver_standings: list[DriverStanding] = []
    constructor_standings: list[ConstructorStanding] = []


# ============================================================================
# Teams & Drivers Models (for season entry list display)
# ============================================================================


class TeamDriverEntry(BaseModel):
    """A driver entry within a team for the season."""

    driver_id: str = Field(default="", description="Driver ID from API")
    driver_code: str = Field(default="", description="Driver code, e.g. VER")
    driver_number: Optional[int] = Field(default=None, description="Permanent race number")
    given_name: str = ""
    family_name: str = ""
    name: str = Field(default="", description="Full display name from Wikipedia")
    nationality: str = ""
    rounds: str = Field(default="All", description="Which rounds the driver races")
    position: Optional[int] = Field(default=None, description="Championship position")
    points: float = Field(default=0.0, description="Championship points")
    wins: int = Field(default=0, description="Number of race wins")


class TeamEntry(BaseModel):
    """A team/constructor entry with its drivers for the season."""

    constructor_id: str = Field(default="", description="Constructor ID from API")
    constructor_name: str = Field(default="", description="Team name, e.g. Red Bull")
    entrant: str = Field(default="", description="Full entrant name with sponsors")
    chassis: str = Field(default="", description="Chassis model, e.g. RB21")
    power_unit: str = Field(default="", description="Power unit, e.g. Honda RBPTH003")
    nationality: str = ""
    position: Optional[int] = Field(default=None, description="Constructor championship position")
    points: float = Field(default=0.0, description="Constructor championship points")
    drivers: list[TeamDriverEntry] = Field(default_factory=list)


class TeamsData(BaseModel):
    """Container for teams and drivers data for a season."""

    season: int
    teams: list[TeamEntry] = Field(default_factory=list)


# ============================================================================
# Performance Metrics Models (Real User Monitoring)
# ============================================================================


class PerfMetricsPayload(BaseModel):
    """Payload for performance metrics from browser."""

    page_path: str = Field(..., max_length=500)
    lcp_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    cls: Optional[float] = Field(default=None, ge=0, le=10)
    fcp_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    ttfb_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    inp_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    connection_type: Optional[str] = Field(default=None, max_length=50)
    device_memory: Optional[float] = Field(default=None, ge=0, le=512)
