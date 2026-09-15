"""Tests for the season-to-track-asset coverage guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_track_assets


def _write_season(root: Path, year: int, races: list[dict]) -> Path:
    """Write a minimal bundled season file and return its path."""
    path = root / "app" / "assets" / "seasons" / f"{year}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"season": str(year), "total_races": len(races), "races": races}),
        encoding="utf-8",
    )
    return path


def _race(circuit_id: str, *, round_value: str | None = "1") -> dict:
    """Build a minimal active or cancelled race payload."""
    race = {
        "raceName": f"{circuit_id} Grand Prix",
        "Circuit": {"circuitId": circuit_id},
    }
    if round_value is not None:
        race["round"] = round_value
    return race


def test_default_paths_check_only_bundled_current_and_next_seasons(tmp_path):
    """Default discovery ignores older files and permits an unpublished next season."""
    old = _write_season(tmp_path, 2029, [])
    current = _write_season(tmp_path, 2030, [])

    assert check_track_assets.default_season_paths(tmp_path, current_year=2030) == [current]
    assert old not in check_track_assets.default_season_paths(tmp_path, current_year=2030)


def test_default_paths_fail_when_neither_current_nor_next_is_bundled(tmp_path):
    """The guard must not silently pass when it did not inspect a season file."""
    with pytest.raises(check_track_assets.SeasonDataError, match="No bundled"):
        check_track_assets.default_season_paths(tmp_path, current_year=2030)


@pytest.mark.parametrize("round_value", [None, "", "0", "not-a-round"])
def test_inactive_or_invalid_rounds_do_not_require_assets(tmp_path, round_value):
    """Preserved cancelled races stay outside active asset coverage."""
    season = _write_season(tmp_path, 2026, [_race("cancelled", round_value=round_value)])

    assert check_track_assets.check_track_assets(tmp_path, [season]) == []


def test_main_returns_nonzero_and_prints_missing_items(tmp_path, monkeypatch, capsys):
    """The command reports actionable failures and exits nonzero for CI."""
    _write_season(tmp_path, 2026, [_race("new_track")])
    _write_catalogue(tmp_path, [])
    monkeypatch.setattr(check_track_assets, "PROJECT_ROOT", tmp_path)

    exit_code = check_track_assets.main(["--years", "2026"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "2 missing requirement(s)" in captured.err
    assert "reviewed commons outline" in captured.err


def test_malformed_active_race_fails_instead_of_being_skipped(tmp_path):
    """An active race without a circuit identifier cannot silently pass coverage."""
    season = _write_season(
        tmp_path,
        2026,
        [{"round": "1", "raceName": "Broken", "Circuit": {}}],
    )

    with pytest.raises(check_track_assets.SeasonDataError, match="missing circuitId"):
        check_track_assets.check_track_assets(tmp_path, [season])


def _write_catalogue(root, names):
    path = root / "app/assets/track_art/catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"geometries": dict.fromkeys(names, {})}))


def test_reviewed_sources_cover_mapped_id(tmp_path):
    season = _write_season(tmp_path, 2026, [_race("vegas")])
    _write_catalogue(tmp_path, ["las_vegas:jules", "las_vegas:commons"])
    assert check_track_assets.check_track_assets(tmp_path, [season]) == []


def test_each_source_is_required_even_with_legacy_raster(tmp_path):
    season = _write_season(tmp_path, 2026, [_race("monza")])
    _write_catalogue(tmp_path, ["monza:jules"])
    legacy = tmp_path / "app/assets/tracks_processed/monza.bmp"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"not licensed")
    missing = check_track_assets.check_track_assets(tmp_path, [season])
    assert len(missing) == 1
    assert "commons" in missing[0].requirement


def test_invalid_catalogue_is_actionable(tmp_path):
    season = _write_season(tmp_path, 2026, [_race("monza")])
    with pytest.raises(check_track_assets.SeasonDataError, match="Invalid open track"):
        check_track_assets.check_track_assets(tmp_path, [season])


def test_bundled_current_calendars_have_both_reviewed_sources():
    """Include runtime-only additions such as Sepang in the reviewed release catalogue."""
    root = Path(__file__).resolve().parents[1]
    seasons = check_track_assets.default_season_paths(root)
    assert check_track_assets.check_track_assets(root, seasons) == []
