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
