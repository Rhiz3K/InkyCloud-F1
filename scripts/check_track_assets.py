#!/usr/bin/env python3
"""Fail when active races do not have rebuildable track artwork and runtime BMPs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# Allow direct execution from the repository root or the scripts directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.track_assets import TRACK_SOURCE_EXTENSIONS, resolve_track_source_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Each runtime palette must be reproducible from its preferred artwork variant or the generic
# source. Keep these suffixes aligned with app.services.asset_preprocessing._PALETTE_SPECS.
SOURCE_VARIANTS: tuple[tuple[str, str], ...] = (
    ("mono", "bw"),
    ("bwr", "bwr"),
    ("bwry", "bwry"),
    ("spectra6", "spectra6"),
)
RUNTIME_ASSET_DIRS: tuple[tuple[str, str], ...] = (
    ("mono", "tracks_processed"),
    ("bwr", "tracks_bwr"),
    ("bwry", "tracks_bwry"),
    ("spectra6", "tracks_spectra6"),
)


class SeasonDataError(ValueError):
    """Raised when a bundled season file cannot be checked safely."""


@dataclass(frozen=True, slots=True)
class ActiveCircuit:
    """Circuit identity and calendar context for one active race."""

    season: str
    round: str
    race_name: str
    source_id: str
    runtime_id: str

    @property
    def label(self) -> str:
        """Return a concise human-readable identifier for diagnostics."""
        mapping = f" -> runtime {self.runtime_id!r}" if self.runtime_id != self.source_id else ""
        return (
            f"season {self.season}, round {self.round}, {self.race_name!r}, "
            f"circuit {self.source_id!r}{mapping}"
        )


@dataclass(frozen=True, slots=True)
class MissingAsset:
    """One missing source or runtime asset requirement."""

    circuit: ActiveCircuit
    requirement: str
    expected: str

    def format(self) -> str:
        """Format this requirement for local and GitHub Actions logs."""
        return f"{self.circuit.label}: missing {self.requirement}; expected {self.expected}"


def has_active_round(race: dict[str, Any]) -> bool:
    """Return whether a race still has a positive calendar round."""
    round_value = race.get("round")
    if round_value in (None, ""):
        return False

    try:
        return int(str(round_value)) > 0
    except (TypeError, ValueError):
        return False


def default_season_paths(project_root: Path, *, current_year: int | None = None) -> list[Path]:
    """Return the bundled current and next season files, ignoring an unpublished next year."""
    year = current_year if current_year is not None else datetime.now(timezone.utc).year
    seasons_dir = project_root / "app" / "assets" / "seasons"
    paths = [seasons_dir / f"{candidate}.json" for candidate in (year, year + 1)]
    bundled_paths = [path for path in paths if path.is_file()]
    if not bundled_paths:
        expected = ", ".join(str(path.relative_to(project_root)) for path in paths)
        raise SeasonDataError(f"No bundled current/next season file found; expected {expected}")
    return bundled_paths


def requested_season_paths(project_root: Path, years: Sequence[int]) -> list[Path]:
    """Resolve explicitly requested years and reject every missing season file."""
    seasons_dir = project_root / "app" / "assets" / "seasons"
    paths = [seasons_dir / f"{year}.json" for year in years]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(project_root)) for path in missing)
        raise SeasonDataError(f"Bundled season file not found: {names}")
    return paths


def load_active_circuits(season_path: Path) -> list[ActiveCircuit]:
    """Load active circuit requirements from one bundled season calendar."""
    try:
        payload = json.loads(season_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeasonDataError(f"Cannot read {season_path}: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("races"), list):
        raise SeasonDataError(f"Malformed season payload in {season_path}: 'races' must be a list")

    season = str(payload.get("season") or season_path.stem)
    circuits: list[ActiveCircuit] = []
    for index, race in enumerate(payload["races"]):
        if not isinstance(race, dict):
            raise SeasonDataError(f"Malformed race #{index + 1} in {season_path}: expected object")
        if not has_active_round(race):
            continue

        circuit = race.get("Circuit")
        if not isinstance(circuit, dict):
            raise SeasonDataError(
                f"Malformed active race #{index + 1} in {season_path}: missing Circuit object"
            )
        source_id = str(circuit.get("circuitId") or "").strip().lower()
        if not source_id:
            raise SeasonDataError(
                f"Malformed active race #{index + 1} in {season_path}: missing circuitId"
            )

        runtime_id = str(CIRCUIT_ID_MAP.get(source_id, source_id)).strip().lower()
        circuits.append(
            ActiveCircuit(
                season=season,
                round=str(race["round"]),
                race_name=str(race.get("raceName") or "unnamed race"),
                source_id=source_id,
                runtime_id=runtime_id,
            )
        )

    return circuits


def _source_expectation(stems: Sequence[str], variant: str) -> str:
    """Describe the exact preferred-variant and generic source fallback candidates."""
    source_stems = [f"{stem}_{variant}" for stem in stems] + list(stems)
    stem_choices = ",".join(source_stems)
    extension_choices = ",".join(
        extension.removeprefix(".") for extension in TRACK_SOURCE_EXTENSIONS
    )
    return f"artwork/tracks/{{{stem_choices}}}.{{{extension_choices}}}"


def find_missing_assets(
    project_root: Path, circuits: Sequence[ActiveCircuit]
) -> list[MissingAsset]:
    """Return all missing rebuild sources and runtime BMPs for active circuits."""
    artwork_dir = project_root / "artwork" / "tracks"
    missing: list[MissingAsset] = []

    for circuit in circuits:
        source_stems = list(dict.fromkeys((circuit.source_id, circuit.runtime_id)))
        for palette, variant in SOURCE_VARIANTS:
            source_path = resolve_track_source_path(
                artwork_dir,
                source_stems,
                variant_suffix=variant,
            )
            if source_path is None or not source_path.is_file():
                missing.append(
                    MissingAsset(
                        circuit=circuit,
                        requirement=(
                            f"{palette} artwork source ({variant!r} variant or generic fallback)"
                        ),
                        expected=_source_expectation(source_stems, variant),
                    )
                )

        for palette, directory in RUNTIME_ASSET_DIRS:
            relative_path = Path("app") / "assets" / directory / f"{circuit.runtime_id}.bmp"
            if not (project_root / relative_path).is_file():
                missing.append(
                    MissingAsset(
                        circuit=circuit,
                        requirement=f"{palette} runtime BMP",
                        expected=str(relative_path),
                    )
                )

    return missing


def check_track_assets(project_root: Path, season_paths: Sequence[Path]) -> list[MissingAsset]:
    """Check all active races in the supplied season files and return missing requirements."""
    circuits = [
        circuit for season_path in season_paths for circuit in load_active_circuits(season_path)
    ]
    return find_missing_assets(project_root, circuits)


def parse_years(value: str) -> list[int]:
    """Parse a comma-separated year list for the command-line interface."""
    try:
        years = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("years must be comma-separated integers") from exc
    if not years:
        raise argparse.ArgumentTypeError("at least one year is required")
    return years


def main(argv: Sequence[str] | None = None) -> int:
    """Run the track-asset coverage check and return a process exit status."""
    parser = argparse.ArgumentParser(
        description="Check active current/next-season circuits for source artwork and runtime BMPs"
    )
    parser.add_argument(
        "--years",
        type=parse_years,
        help="Comma-separated bundled seasons (default: current and next year when present)",
    )
    args = parser.parse_args(argv)

    try:
        season_paths = (
            requested_season_paths(PROJECT_ROOT, args.years)
            if args.years is not None
            else default_season_paths(PROJECT_ROOT)
        )
        missing = check_track_assets(PROJECT_ROOT, season_paths)
    except SeasonDataError as exc:
        print(f"Track asset coverage check failed: {exc}", file=sys.stderr)
        return 1

    checked = ", ".join(path.stem for path in season_paths)
    if missing:
        print(
            f"Track asset coverage check failed for seasons {checked}: "
            f"{len(missing)} missing requirement(s)",
            file=sys.stderr,
        )
        for item in missing:
            print(f"  - {item.format()}", file=sys.stderr)
        return 1

    print(f"Track asset coverage is complete for active circuits in seasons {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
