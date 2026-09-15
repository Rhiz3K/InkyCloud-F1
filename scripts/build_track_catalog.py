"""Rebuild reviewed open circuit commands offline: uv run scripts/build_track_catalog.py --check."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from svgelements import Arc, Close, CubicBezier, Line, Move, QuadraticBezier
from svgelements import Path as SvgPath

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "artwork" / "open-tracks"
OUTPUT = ROOT / "app" / "assets" / "track_art" / "catalog.json"
ALLOWED_LICENSES = {"CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0", "CC0-1.0"}


def vector_commands(d: str) -> list[list]:
    """Keep Beziers exact; approximate only arcs with cubic spans of at most 7.5 degrees."""
    if len(d) > 1_000_000 or not re.fullmatch(r"[MmZzLlHhVvCcSsQqTtAa0-9eE+.,\s-]+", d):
        raise ValueError("Unsafe or oversized path data")
    commands = []
    for segment in SvgPath(d):
        pieces = (
            segment.as_cubic_curves(max(1, math.ceil(abs(segment.sweep) / math.radians(7.5))))
            if isinstance(segment, Arc)
            else [segment]
        )
        for piece in pieces:
            if isinstance(piece, Close):
                commands.append(["Z"])
                continue
            if isinstance(piece, Move):
                command, points = "M", [piece.end]
            elif isinstance(piece, Line):
                command, points = "L", [piece.end]
            elif isinstance(piece, QuadraticBezier):
                command, points = "Q", [piece.control, piece.end]
            elif isinstance(piece, CubicBezier):
                command, points = "C", [piece.control1, piece.control2, piece.end]
            else:
                raise ValueError("Unsupported path segment")
            coordinates = [round(value, 7) for point in points for value in (point.x, point.y)]
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError("Non-finite path coordinate")
            commands.append([command, *coordinates])
    if not commands or commands[-1] != ["Z"] or sum(c[0] == "M" for c in commands) != 1:
        raise ValueError("Expected one explicitly selected, closed outline")
    return commands


def build_catalog() -> bytes:
    """Verify pinned originals and licences before compiling the reviewed path selection."""
    catalogue = json.loads((SOURCES / "catalog-source.json").read_text())
    jules = json.loads((SOURCES / "all-tracks-manifest.json").read_text())["tracks"]
    commons = json.loads((SOURCES / "commons-source-manifest.json").read_text())
    manifests = {
        f"{item['circuit_id']}:{family}": item
        for family, items in (("jules", jules), ("commons", commons))
        for item in items
    }
    circuits = json.loads((ROOT / "app" / "assets" / "circuits_data.json").read_text())
    if set(circuits) != {track["id"] for track in catalogue["tracks"]}:
        raise ValueError("Reviewed catalogue must cover every application circuit")
    expected = {f"{circuit}:{family}" for circuit in circuits for family in ("jules", "commons")}
    if set(catalogue["geometries"]) != expected:
        raise ValueError("Both reviewed source families are required for every circuit")
    for key, geometry in catalogue["geometries"].items():
        manifest = manifests[key]
        path = (SOURCES / manifest["source_file"]).resolve()
        if not path.is_relative_to(SOURCES.resolve()):
            raise ValueError("Original source path escapes the archive")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != geometry["source_sha256"] or digest != manifest["source_sha256"]:
            raise ValueError(f"Changed original requires source review: {key}")
        if geometry["license"] not in ALLOWED_LICENSES:
            raise ValueError(f"Unreviewed licence: {key}")
        for field in ("author", "license", "license_url", "source", "attribution_sources"):
            if geometry.get(field) != manifest.get(field):
                raise ValueError(f"Attribution differs from reviewed manifest: {key}: {field}")
        geometry["commands"] = vector_commands(geometry["d"])
    return (
        json.dumps(catalogue, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def main() -> None:
    """Compare the reproducible bundle in CI, or explicitly rebuild it for source review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = build_catalog()
    if args.check:
        if OUTPUT.read_bytes() != data:
            raise SystemExit("Track catalogue is stale; run uv run scripts/build_track_catalog.py")
        count = len(json.loads(data)["geometries"])
        print(f"{count} reviewed outlines: checksums, attribution and generated commands verified")
    else:
        OUTPUT.write_bytes(data)
        print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
