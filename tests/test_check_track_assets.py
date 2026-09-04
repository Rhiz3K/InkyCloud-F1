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


def _write_runtime_assets(root: Path, runtime_id: str, *, omit: str | None = None) -> None:
    """Create the four expected runtime BMP placeholders except an optional palette."""
    for palette, directory in check_track_assets.RUNTIME_ASSET_DIRS:
        if palette == omit:
            continue
        path = root / "app" / "assets" / directory / f"{runtime_id}.bmp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"BMP")


def test_generic_source_and_mapped_runtime_id_cover_all_palettes(tmp_path):
    """A generic API-ID source may build mapped Las Vegas runtime assets."""
    season = _write_season(
        tmp_path,
        2026,
        [_race("vegas"), _race("missing_cancelled", round_value=None)],
    )
    source = tmp_path / "artwork" / "tracks" / "vegas.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"PNG")
    _write_runtime_assets(tmp_path, "las_vegas")

    assert check_track_assets.check_track_assets(tmp_path, [season]) == []


def test_palette_variants_are_valid_without_generic_source(tmp_path):
    """Each palette-specific artwork file can replace the generic source fallback."""
    season = _write_season(tmp_path, 2026, [_race("test_ring")])
    source_dir = tmp_path / "artwork" / "tracks"
    source_dir.mkdir(parents=True)
    for _palette, variant in check_track_assets.SOURCE_VARIANTS:
        (source_dir / f"test_ring_{variant}.png").write_bytes(b"PNG")
    _write_runtime_assets(tmp_path, "test_ring")

    assert check_track_assets.check_track_assets(tmp_path, [season]) == []


def test_missing_requirements_report_source_fallback_and_exact_runtime_path(tmp_path):
    """Diagnostics identify every unavailable source palette and exact missing BMP path."""
    season = _write_season(tmp_path, 2026, [_race("new_track", round_value="4")])
    source_dir = tmp_path / "artwork" / "tracks"
    source_dir.mkdir(parents=True)
    (source_dir / "new_track_bwr.png").write_bytes(b"PNG")
    _write_runtime_assets(tmp_path, "new_track", omit="bwry")

    missing = check_track_assets.check_track_assets(tmp_path, [season])
    formatted = [item.format() for item in missing]

    assert len(missing) == 4
    assert any(
        "mono artwork source" in item and "new_track_bw,new_track" in item
        for item in formatted
    )
    assert not any("bwr artwork source" in item for item in formatted)
    assert any("bwry artwork source" in item for item in formatted)
    assert any("spectra6 artwork source" in item for item in formatted)
    assert any(
        "bwry runtime BMP" in item
        and "app/assets/tracks_bwry/new_track.bmp" in item
        for item in formatted
    )
    assert all("season 2026, round 4" in item for item in formatted)


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
    (tmp_path / "artwork" / "tracks").mkdir(parents=True)
    monkeypatch.setattr(check_track_assets, "PROJECT_ROOT", tmp_path)

    exit_code = check_track_assets.main(["--years", "2026"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "8 missing requirement(s)" in captured.err
    assert "tracks_spectra6/new_track.bmp" in captured.err


def test_malformed_active_race_fails_instead_of_being_skipped(tmp_path):
    """An active race without a circuit identifier cannot silently pass coverage."""
    season = _write_season(
        tmp_path,
        2026,
        [{"round": "1", "raceName": "Broken", "Circuit": {}}],
    )

    with pytest.raises(check_track_assets.SeasonDataError, match="missing circuitId"):
        check_track_assets.check_track_assets(tmp_path, [season])
