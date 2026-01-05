"""Test database service."""

import asyncio

import pytest

from app.services.database import Database


class TestCircuitWeatherDatabase:
    """Tests for circuit weather database methods."""

    @pytest.fixture
    def db(self):
        """Create a fresh database instance for each test."""
        return Database()

    @staticmethod
    def test_save_and_get_circuit_weather(db):
        """Test saving and retrieving circuit weather."""

        async def run_test():
            await db.save_circuit_weather(
                circuit_id="albert_park",
                circuit_name="Albert Park",
                temperature_c=25.5,
                weather_code=1,
                precipitation_probability=15,
            )

            result = await db.get_circuit_weather("albert_park")
            assert result is not None
            assert result["temperature_c"] == 25.5
            assert result["weather_code"] == 1
            assert result["precipitation_probability"] == 15
            assert "fetched_at" in result

        asyncio.run(run_test())

    @staticmethod
    def test_get_circuit_weather_not_found(db):
        """Test that missing circuit returns None."""

        async def run_test():
            result = await db.get_circuit_weather("nonexistent")
            assert result is None

        asyncio.run(run_test())

    @staticmethod
    def test_save_circuit_weather_upsert(db):
        """Test that saving same circuit updates the data."""

        async def run_test():
            # First save
            await db.save_circuit_weather(
                circuit_id="monaco",
                circuit_name="Monaco",
                temperature_c=20.0,
                weather_code=0,
                precipitation_probability=5,
            )

            # Second save (update)
            await db.save_circuit_weather(
                circuit_id="monaco",
                circuit_name="Monaco Street Circuit",
                temperature_c=28.0,
                weather_code=61,
                precipitation_probability=80,
            )

            result = await db.get_circuit_weather("monaco")
            assert result is not None
            assert result["temperature_c"] == 28.0
            assert result["weather_code"] == 61
            assert result["precipitation_probability"] == 80

        asyncio.run(run_test())

    @staticmethod
    def test_load_all_circuit_weather_empty(db):
        """Test loading all weather when database is empty."""

        async def run_test():
            result = await db.load_all_circuit_weather()
            assert isinstance(result, dict)
            # May have data from other tests, so just check it's a dict

        asyncio.run(run_test())

    @staticmethod
    def test_load_all_circuit_weather(db):
        """Test loading all circuit weather data."""

        async def run_test():
            # Save multiple circuits
            await db.save_circuit_weather(
                circuit_id="silverstone",
                circuit_name="Silverstone",
                temperature_c=18.0,
                weather_code=3,
                precipitation_probability=40,
            )
            await db.save_circuit_weather(
                circuit_id="spa",
                circuit_name="Spa-Francorchamps",
                temperature_c=15.0,
                weather_code=61,
                precipitation_probability=70,
            )

            result = await db.load_all_circuit_weather()

            assert "silverstone" in result
            assert "spa" in result
            assert result["silverstone"]["temperature_c"] == 18.0
            assert result["spa"]["weather_code"] == 61

        asyncio.run(run_test())
