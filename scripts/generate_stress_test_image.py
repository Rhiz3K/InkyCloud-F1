import os
import sys
from datetime import datetime

# Add app to path
sys.path.append(os.getcwd())

from app.models import (
    ConstructorInfo,
    DriverInfo,
    HistoricalData,
    QualifyingResultEntry,
    RaceResultEntry,
)
from app.services.renderer import Renderer


def generate_stress_image():
    # Longest values found + some extra stress testing
    long_race_name = "70th Anniversary Grand Prix"
    long_circuit_name = "Autódromo Internacional Nelson Piquet"
    long_locality = "Eastern Cape Province"
    long_country = "South Africa"
    long_driver_given = "Alberto Rodriguez"
    long_driver_family = "Montgomerie-Charrington"
    long_driver_code = "AIT"  # From script
    long_constructor = "Lotus-Pratt & Whitney"

    # Combine driver name for display
    # Renderer uses driver.display_name property (e.g., "J. Doe")
    # Checking models.py would be good, but assuming standard logic.

    race_data = {
        "race_name": long_race_name,
        "season": "2024",
        "circuit": {
            "circuitId": "monza",  # Use real circuit for circuit stats
            "name": long_circuit_name,
            "location": long_locality,
            "country": long_country,
        },
        "schedule": [
            {
                "name": "FirstPractice",
                "datetime": datetime(2024, 5, 17, 13, 30),
                "display_time": "13:30",
            },
            {
                "name": "SprintQualifying",
                "datetime": datetime(2024, 5, 17, 17, 30),
                "display_time": "17:30",
            },
            {
                "name": "Sprint",
                "datetime": datetime(2024, 5, 18, 12, 0),
                "display_time": "12:00",
            },
            {
                "name": "Qualifying",
                "datetime": datetime(2024, 5, 18, 16, 0),
                "display_time": "16:00",
            },
            {
                "name": "Race",
                "datetime": datetime(2024, 5, 19, 15, 0),
                "display_time": "15:00",
            },
        ],
    }

    historical_data = HistoricalData(
        season=2023,
        is_new_track=False,
        race_results=[
            RaceResultEntry(
                position=1,
                driver=DriverInfo(
                    code=long_driver_code,
                    given_name=long_driver_given,
                    family_name=long_driver_family,
                ),
                constructor=ConstructorInfo(name=long_constructor),
                time="1:33:56.736",
            ),
            RaceResultEntry(
                position=2,
                driver=DriverInfo(
                    code="AA",
                    given_name="VeryLongName",
                    family_name="EvenLongerSurname",
                ),
                constructor=ConstructorInfo(name="Aston Martin Aramco"),
                time="+11.987",
            ),
            RaceResultEntry(
                position=3,
                driver=DriverInfo(code="BB", given_name="Test", family_name="Driver"),
                constructor=ConstructorInfo(name="Racing Bulls Honda RBPT"),
                time="+38.637",
            ),
        ],
        qualifying_results=[
            QualifyingResultEntry(
                position=1,
                driver=DriverInfo(
                    code=long_driver_code,
                    given_name=long_driver_given,
                    family_name=long_driver_family,
                ),
                constructor=ConstructorInfo(name=long_constructor),
                q3_time="1:29.708",
            ),
            QualifyingResultEntry(
                position=20,
                driver=DriverInfo(
                    code="RIC", given_name="Daniel", family_name="Ricciardo"
                ),
                constructor=ConstructorInfo(name="Visa Cash App RB"),
                q3_time="1:29.846",
            ),
            QualifyingResultEntry(
                position=3,
                driver=DriverInfo(code="TSU", given_name="Yuki", family_name="Tsunoda"),
                constructor=ConstructorInfo(name="AlphaTauri Honda"),
                q3_time="1:30.000",
            ),
        ],
    )

    # Use default translator but ensure keys exist
    translator = {
        "weekend_schedule": "WEEKEND SCHEDULE",
        "qualifying": "QUALIFYING",
        "race": "RACE",
        "session_firstpractice": "Practice 1",
        "session_secondpractice": "Practice 2",
        "session_thirdpractice": "Practice 3",
        "session_sprintqualifying": "Sprint-Q",
        "session_sprint": "Sprint",
        "session_qualifying": "Qualifying",
        "session_race": "Race",
        "day_fri": "Fri",
        "day_sat": "Sat",
        "day_sun": "Sun",
        "error": "Error",
        "new_track": "NEW TRACK",
        "day_monday": "Mon",
        "day_tuesdau": "Tue",
        "day_wednesday": "Wed",
        "day_thursday": "Thu",
        "day_friday": "Fri",
        "day_saturday": "Sat",
        "day_sunday": "Sun",
        "laps": "laps",
        "first_gp": "First GP",
    }

    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(race_data, historical_data)

    output_path = "test_calendar_stress.bmp"
    with open(output_path, "wb") as f:
        f.write(bmp_data)
    print(f"Generated {output_path} ({len(bmp_data)} bytes)")


if __name__ == "__main__":
    generate_stress_image()
