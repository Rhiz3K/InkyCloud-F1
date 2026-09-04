"""Tests for the season-to-track-asset coverage guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import track_assets
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


def _write_managed_bundle(root: Path, circuit_id: str = "managed") -> Path:
    """Write a complete managed source bundle and its provenance manifest."""
    source_dir = root / "artwork" / "tracks"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths = track_assets.track_bundle_paths(source_dir, circuit_id)
    for variant, path in paths.items():
        path.write_bytes(f"PNG:{variant}".encode())
    source_sha = "a" * 64
    manifest = {
        "schema_version": 1,
        "tracks": {
            circuit_id: {
                "season": 2026,
                "race_slug": "managed-race",
                "source_page": "https://www.formula1.com/en/racing/2026/managed-race",
                "source_url": "https://media.formula1.com/image/upload/managed.png",
                "source_sha256": source_sha,
                "source_dimensions": [3840, 2160],
                "source_profile": "modern",
                "sector_boundaries": [
                    {"at": [0.3, 0.5], "normal_degrees": 90},
                    {"at": [0.6, 0.5], "normal_degrees": 90},
                ],
                "rights_review_required": True,
            }
        },
    }
    (source_dir / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    png_bytes = {variant: path.read_bytes() for variant, path in paths.items()}
    marker = track_assets.encode_track_bundle_marker(circuit_id, source_sha, png_bytes)
    (source_dir / f"{circuit_id}.bundle.json").write_bytes(marker)
    return source_dir


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


def test_valid_managed_bundle_passes_coverage(tmp_path):
    """Coverage accepts a manifest-managed track only after every bundle hash matches."""
    season = _write_season(tmp_path, 2026, [_race("managed")])
    _write_managed_bundle(tmp_path)
    _write_runtime_assets(tmp_path, "managed")

    assert check_track_assets.check_track_assets(tmp_path, [season]) == []


@pytest.mark.parametrize("corruption", ["missing-marker", "tampered-marker"])
def test_invalid_managed_bundle_fails_coverage(tmp_path, corruption):
    """A missing marker or altered recorded digest must produce an actionable failure."""
    season = _write_season(tmp_path, 2026, [_race("managed")])
    source_dir = _write_managed_bundle(tmp_path)
    marker_path = source_dir / "managed.bundle.json"
    if corruption == "missing-marker":
        marker_path.unlink()
    else:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["files"]["bwr"] = "0" * 64
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    _write_runtime_assets(tmp_path, "managed")

    missing = check_track_assets.check_track_assets(tmp_path, [season])

    assert len(missing) == 1
    assert "valid managed artwork bundle" in missing[0].requirement
    assert missing[0].expected == "artwork/tracks/managed.bundle.json"


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
        "mono artwork source" in item and "new_track_bw,new_track" in item for item in formatted
    )
    assert not any("bwr artwork source" in item for item in formatted)
    assert any("bwry artwork source" in item for item in formatted)
    assert any("spectra6 artwork source" in item for item in formatted)
    assert any(
        "bwry runtime BMP" in item and "app/assets/tracks_bwry/new_track.bmp" in item
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
