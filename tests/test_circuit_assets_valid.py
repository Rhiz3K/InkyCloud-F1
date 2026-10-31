"""Validate the committed circuit assets against the manual refresh contract."""

import json
from datetime import date
from pathlib import Path

from scripts import fetch_circuits as fc


def test_committed_circuit_assets():
    """Check IDs, non-null facts, complete lap records and revision provenance."""
    root = Path(__file__).resolve().parents[1] / "app" / "assets"
    expected = {cid for cid, _, _ in fc.CIRCUITS}
    current_year = date.today().year
    for source in ("wikipedia", "jolpica"):
        records = json.loads((root / f"circuits_{source}.json").read_text(encoding="utf-8"))
        assert set(records) == expected
        for record in records.values():
            for field, validator in (
                ("circuit_length", fc.valid_length),
                ("fastest_lap_time", fc.valid_time),
                ("fastest_lap_driver", fc.valid_driver),
            ):
                if record.get(field) is not None:
                    assert validator(record[field])
            if record.get("fastest_lap_year") is not None:
                assert fc.valid_year(record["fastest_lap_year"], current_year)
            if record.get("first_grand_prix") is not None:
                assert fc.valid_year(str(record["first_grand_prix"]), current_year)
            if record.get("number_of_laps") is not None:
                assert fc.valid_laps(str(record["number_of_laps"]))
            lap_record = [record.get(field) is not None for field in fc.FIELDS[1:]]
            assert all(lap_record) or not any(lap_record)
            if source == "wikipedia":
                for field in fc.FIELDS:
                    if record.get(field) is not None:
                        provenance = record["_provenance"]["fields"][field]
                        assert provenance["source"].startswith("https://en.wikipedia.org/")
                        assert "oldid=" in provenance["source"]
                        assert type(provenance["revid"]) is int
