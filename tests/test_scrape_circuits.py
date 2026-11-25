"""Regression coverage for retiring the unlicensed circuit scraper."""

from pathlib import Path


def test_legacy_scraper_is_not_distributed():
    """The maintenance entrypoint cannot fetch F1 circuit metadata again."""
    assert not Path("scripts/scrape_circuits.py").exists()


def test_circuit_assets_have_separate_licenses_and_provenance():
    """Every bundled fact has an appropriate license and pinned revision attribution."""
    import json

    assets = Path("app/assets")
    assert not (assets / "circuits_data.json").exists()
    records = {}
    for source, license_id in (("jolpica", "CC-BY-NC-SA-4.0"), ("wikipedia", "CC-BY-SA-4.0")):
        path = assets / f"circuits_{source}.json"
        assert "LicenseRef-Legacy-Circuit-Facts-Unverified" not in path.read_text()
        records[source] = json.loads(path.read_text())
        assert (
            f"SPDX-License-Identifier: {license_id}"
            in path.with_suffix(".json.license").read_text()
        )
    assert len(records["jolpica"]) == 26
    assert records["jolpica"].keys() == records["wikipedia"].keys()
    assert (
        "LicenseRef-Legacy-Circuit-Facts-Unverified"
        not in (assets / "asset-register.json").read_text()
    )
    for record in records["jolpica"].values():
        assert not any(key == "circuit_length" or key.startswith("fastest_lap_") for key in record)
    for record in records["wikipedia"].values():
        for field in (
            "circuit_length",
            "fastest_lap_time",
            "fastest_lap_driver",
            "fastest_lap_year",
        ):
            if record[field] is not None:
                provenance = record["_provenance"]["fields"][field]
                assert provenance["source"].startswith(
                    "https://en.wikipedia.org/w/index.php?title="
                )
                assert "&oldid=" in provenance["source"]
                assert type(provenance["revid"]) is int
