"""Shared, persistent request budget for the free noncommercial weather API."""

from app.services.database import get_database


class WeatherRequestBudget:
    """Reserve quota through the database, without an event-loop-bound pacing lock."""

    @staticmethod
    async def wait() -> None:
        """Fail promptly so the caller can display the calendar without weather."""
        if not await get_database().reserve_weather_request():
            raise RuntimeError("Open-Meteo request quota exhausted")


WEATHER_BUDGET = WeatherRequestBudget()
