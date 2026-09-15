"""Regression coverage for retiring the unlicensed circuit scraper."""

from pathlib import Path

from app.services.circuit_data import load_circuits_data


def test_legacy_scraper_is_not_distributed():
    """The maintenance entrypoint cannot fetch F1 circuit metadata again."""
    assert not Path("scripts/scrape_circuits.py").exists()


def test_restored_circuit_facts_keep_their_separate_provenance(tmp_path):
    """Restoring the display must not relabel legacy facts as Jolpica-licensed data."""
    records = load_circuits_data(tmp_path / "absent.json")
    assert records["albert_park"]["circuit_length"] == "5.278km"
    assert records["albert_park"]["fastest_lap_time"] == "1:19.813"
    for record in records.values():
        assert record["_provenance"]["scope"] == "historical"
        if record.get("circuit_length"):
            provenance = record["_provenance"]["supplementary"]
            assert provenance["license"] == "LicenseRef-Legacy-Circuit-Facts-Unverified"
            assert provenance["source"].startswith("https://github.com/Rhiz3K/InkyCloud-F1/blob/")
    # Sepang is an extra artwork circuit, without legacy fact data to restore.
    assert records["sepang"]["circuit_length"] is None
