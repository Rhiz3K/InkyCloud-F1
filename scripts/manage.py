"""Unified maintenance CLI for repository asset workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.services.asset_preprocessing import (
    PREPROCESS_PALETTES,
    PreprocessingError,
    preprocess_flags,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the maintenance command hierarchy."""
    parser = argparse.ArgumentParser(prog="python -m scripts.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    preprocess = commands.add_parser("preprocess", help="regenerate display assets")
    asset_types = preprocess.add_subparsers(dest="asset_type", required=True)

    flags = asset_types.add_parser("flags", help="preprocess flat flag artwork")
    flags.add_argument("--palette", choices=PREPROCESS_PALETTES, required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a maintenance command and return its process exit code."""
    args = build_parser().parse_args(argv)
    try:
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
