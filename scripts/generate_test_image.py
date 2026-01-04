import json
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


def generate_test_image():
    # Mock Race Data - China 2026 (Sprint Weekend)
    race_data = {
        "race_name": "Chinese",
        "season": "2026",
        "circuit": {
            "circuitId": "shanghai",
            "name": "Shanghai International Circuit",
            "location": "Shanghai",
            "country": "China",
        },
        "schedule": [
            {
                "name": "FirstPractice",
                "datetime": datetime(2026, 3, 13, 3, 30),
                "display_time": "03:30",
            },
            {
                "name": "SprintQualifying",
                "datetime": datetime(2026, 3, 13, 7, 30),
                "display_time": "07:30",
            },
            {
                "name": "Sprint",
                "datetime": datetime(2026, 3, 14, 3, 0),
                "display_time": "03:00",
            },
            {
                "name": "Qualifying",
                "datetime": datetime(2026, 3, 14, 7, 0),
                "display_time": "07:00",
            },
            {
                "name": "Race",
                "datetime": datetime(2026, 3, 15, 7, 0),
                "display_time": "07:00",
            },
        ],
    }

    # Mock Historical Data
    historical_data = HistoricalData(
        season=2023,
        is_new_track=False,
        race_results=[
            RaceResultEntry(
                position=1,
                driver=DriverInfo(code="VER", given_name="Max", family_name="Verstappen"),
                constructor=ConstructorInfo(name="Red Bull"),
                time="1:33:56.736",
            ),
            RaceResultEntry(
                position=2,
                driver=DriverInfo(code="PER", given_name="Sergio", family_name="Perez"),
                constructor=ConstructorInfo(name="Red Bull"),
                time="+11.987",
            ),
            RaceResultEntry(
                position=3,
                driver=DriverInfo(code="ALO", given_name="Fernando", family_name="Alonso"),
                constructor=ConstructorInfo(name="Aston Martin"),
                time="+38.637",
            ),
        ],
        qualifying_results=[
            QualifyingResultEntry(
                position=1,
                driver=DriverInfo(code="VER", given_name="Max", family_name="Verstappen"),
                constructor=ConstructorInfo(name="Red Bull"),
                q3_time="1:29.708",
            ),
            QualifyingResultEntry(
                position=2,
                driver=DriverInfo(code="PER", given_name="Sergio", family_name="Perez"),
                constructor=ConstructorInfo(name="Red Bull"),
                q3_time="1:29.846",
            ),
            QualifyingResultEntry(
                position=3,
                driver=DriverInfo(code="LEC", given_name="Charles", family_name="Leclerc"),
                constructor=ConstructorInfo(name="Ferrari"),
                q3_time="1:30.000",
            ),
        ],
    )

    # Mock Translator
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
    }

    # English Image
    renderer_en = Renderer(translator)
    bmp_data_en = renderer_en.render_calendar(race_data, historical_data)

    output_path_en = "test_calendar.bmp"
    with open(output_path_en, "wb") as f:
        f.write(bmp_data_en)
    print(f"Generated {output_path_en} ({len(bmp_data_en)} bytes)")

    # Czech Image
    # Load CS translations
    try:
        with open("translations/cs.json", "r", encoding="utf-8") as f:
            translator_cs = json.load(f)

        # Ensure fallback keys if missing in json (optional, but good for testing)

        renderer_cs = Renderer(translator_cs)
        bmp_data_cs = renderer_cs.render_calendar(race_data, historical_data)

        output_path_cs = "test_calendar_cs.bmp"
        with open(output_path_cs, "wb") as f:
            f.write(bmp_data_cs)
        print(f"Generated {output_path_cs} ({len(bmp_data_cs)} bytes)")

    except Exception as e:
        print(f"Failed to generate CS image: {e}")


if __name__ == "__main__":
    generate_test_image()
