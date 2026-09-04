"""Unified maintenance CLI for repository asset workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.services.asset_preprocessing import (
    PREPROCESS_PALETTES,
    PreprocessingError,
    preprocess_flags,
    preprocess_tracks,
)
from app.services.track_artwork import (
    DEFAULT_TRACK_MANIFEST,
    import_track_artwork,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the maintenance command hierarchy."""
    parser = argparse.ArgumentParser(prog="python -m scripts.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    preprocess = commands.add_parser("preprocess", help="regenerate display assets")
    asset_types = preprocess.add_subparsers(dest="asset_type", required=True)

    tracks = asset_types.add_parser("tracks", help="preprocess track artwork")
    tracks.add_argument("--palette", choices=PREPROCESS_PALETTES, required=True)
    tracks.add_argument("--circuits", help="comma-separated source circuit IDs")

    flags = asset_types.add_parser("flags", help="preprocess flat flag artwork")
    flags.add_argument("--palette", choices=PREPROCESS_PALETTES, required=True)

    import_command = commands.add_parser("import", help="import validated local source artwork")
    import_types = import_command.add_subparsers(dest="asset_type", required=True)
    track_import = import_types.add_parser("track", help="import official F1 track artwork")
    track_import.add_argument("--source", type=Path, required=True, help="local original PNG")
    track_import.add_argument("--circuit", required=True, help="manifest circuit ID")
    track_import.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_TRACK_MANIFEST,
        help="versioned source manifest",
    )
    track_import.add_argument(
        "--expected-sha256",
        help="optional second check; must also match the manifest SHA-256",
    )
    track_import.add_argument(
        "--preprocess",
        action="store_true",
        help="regenerate runtime BMPs for all display palettes after import",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a maintenance command and return its process exit code."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "import":
            result = import_track_artwork(
                args.source,
                args.circuit,
                manifest_path=args.manifest,
                expected_sha256=args.expected_sha256,
            )
            print(
                f"Imported {result.circuit_id}: {result.output_dimensions[0]}x"
                f"{result.output_dimensions[1]}, SHA-256 {result.source_sha256}"
            )
            if args.preprocess:
                for palette in PREPROCESS_PALETTES:
                    preprocess_tracks(palette, [result.circuit_id])
        elif args.asset_type == "tracks":
            circuits = args.circuits.split(",") if args.circuits else None
            preprocess_tracks(args.palette, circuits)
        else:
            preprocess_flags(args.palette)
    except (PreprocessingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def legacy_main(asset_type: str, palette: str) -> None:
    """Forward an old script name to the unified CLI during the transition."""
    raise SystemExit(main(["preprocess", asset_type, "--palette", palette, *sys.argv[1:]]))


if __name__ == "__main__":
    raise SystemExit(main())
