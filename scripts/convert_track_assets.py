#!/usr/bin/env python3
"""Download and convert track assets for 1-bit and Spectra6 outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from track_conversion_utils import (
    build_1bit_track,
    convert_spectra6_track,
    download_file,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_F1_TRACK_URLS = {
    "albert_park": "https://media.formula1.com/image/upload/f_auto/q_auto/"
    "v1751632426/common/f1/2026/track/2026trackmelbournedetailed.png",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert track source image into 1-bit and Spectra6 BMP assets."
    )
    parser.add_argument(
        "--circuit-id",
        default="albert_park",
        help="Circuit id used to build default paths (default: albert_park)",
    )
    parser.add_argument(
        "--source-path",
        type=Path,
        help="Path to source PNG/JPG (default: app/assets/tracks/<circuit-id>.png)",
    )
    parser.add_argument(
        "--source-url",
        help="Optional URL to download source image before conversion",
    )
    parser.add_argument(
        "--download-default-url",
        action="store_true",
        help="Download known default F1 URL for the selected circuit-id",
    )
    parser.add_argument(
        "--track-out",
        type=Path,
        help="Output 1-bit BMP path (default: app/assets/tracks_processed/<circuit-id>.bmp)",
    )
    parser.add_argument(
        "--spectra-out",
        type=Path,
        help="Output Spectra6 BMP path (default: app/assets/tracks_spectra6/<circuit-id>.bmp)",
    )
    parser.add_argument(
        "--skip-1bit",
        action="store_true",
        help="Skip 1-bit conversion",
    )
    parser.add_argument(
        "--skip-spectra6",
        action="store_true",
        help="Skip Spectra6 conversion",
    )
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    source_path = args.source_path or (
        PROJECT_ROOT / "app" / "assets" / "tracks" / f"{args.circuit_id}.png"
    )
    track_out = args.track_out or (
        PROJECT_ROOT / "app" / "assets" / "tracks_processed" / f"{args.circuit_id}.bmp"
    )
    spectra_out = args.spectra_out or (
        PROJECT_ROOT / "app" / "assets" / "tracks_spectra6" / f"{args.circuit_id}.bmp"
    )
    return source_path, track_out, spectra_out


def main() -> int:
    args = parse_args()
    source_path, track_out, spectra_out = resolve_paths(args)

    source_url = args.source_url
    if (
        source_url is None
        and args.download_default_url
        and args.circuit_id in DEFAULT_F1_TRACK_URLS
    ):
        source_url = DEFAULT_F1_TRACK_URLS[args.circuit_id]

    if source_url:
        bytes_downloaded = download_file(source_url, source_path)
        print(f"Downloaded source: {source_url}")
        print(f"Saved to: {source_path} ({bytes_downloaded} bytes)")

    if not source_path.exists():
        print(f"Source file not found: {source_path}")
        return 1

    print(f"Using source: {source_path}")

    if not args.skip_1bit:
        onebit_stats = build_1bit_track(source_path, track_out)
        print("1-bit conversion complete")
        print(f"  Output: {track_out}")
        print(f"  Size: {onebit_stats['final_dimensions']}")
        print(f"  Black ratio: {onebit_stats['black_ratio']:.4f}")
        print(f"  Bytes: {onebit_stats['output_size']}")

    if not args.skip_spectra6:
        spectra_stats = convert_spectra6_track(source_path, spectra_out)
        print("Spectra6 conversion complete")
        print(f"  Output: {spectra_out}")
        print(f"  Size: {spectra_stats['final_dimensions']}")
        print(f"  Colors used: {spectra_stats['colors_used']}")
        print(f"  Bytes: {spectra_stats['output_size']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
