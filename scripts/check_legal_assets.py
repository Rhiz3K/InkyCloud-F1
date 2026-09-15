"""Verify the reviewed public asset inventory in the tree and release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app/assets"
REGISTER = ASSETS / "asset-register.json"
RETIRED = (
    "artwork/tracks/",
    "assets/",
    "app/assets/tracks_",
    "app/assets/images/drivers/",
)


def inspect_assets(assets: dict[str, bytes], register: dict) -> list[str]:
    """Reject unknown, missing or changed bytes instead of approving new inputs."""
    errors = []
    for name in sorted(assets.keys() | register.keys()):
        if name not in register:
            errors.append(f"Unregistered asset: {name}")
        elif name not in assets:
            errors.append(f"Missing asset: {name}")
        elif hashlib.sha256(assets[name]).hexdigest() != register[name]["sha256"]:
            errors.append(f"Changed asset: {name}")
        elif not all(register[name].get(key) for key in ("source", "license", "author")):
            errors.append(f"Incomplete attribution: {name}")
    return errors


def archive_records(path: Path) -> dict[str, bytes]:
    """Read actual release files without extracting paths or following archive links."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {
                name: archive.read(name) for name in archive.namelist() if not name.endswith("/")
            }
    with tarfile.open(path) as archive:
        records = {}
        for entry in archive:
            if entry.issym() or entry.islnk():
                raise ValueError(f"Release archive contains a link: {entry.name}")
            if entry.isfile():
                with archive.extractfile(entry) as source:
                    records[entry.name.split("/", 1)[-1]] = source.read()
        return records


def inspect_release(records: dict[str, bytes], register: dict, forbidden: set[str]) -> list[str]:
    """Check release paths, embedded runtime allowlist, exact assets and withdrawn hashes."""
    assets = {
        name.removeprefix("app/assets/"): data
        for name, data in records.items()
        if name.startswith("app/assets/") and name != "app/assets/asset-register.json"
    }
    errors = inspect_assets(assets, register)
    embedded = json.loads(records.get("app/assets/asset-register.json", b"{}"))
    if embedded.get("assets") != register:
        errors.append("Embedded asset register differs from reviewed source")
    for name, data in records.items():
        if name.startswith(RETIRED) or hashlib.sha256(data).hexdigest() in forbidden:
            errors.append(f"Withdrawn asset reintroduced: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Check the tree and optional wheel/source archives against the reviewed register."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="*", type=Path)
    args = parser.parse_args(argv)
    register = json.loads(REGISTER.read_text())["assets"]
    records = {
        str(path.relative_to(ROOT)): path.read_bytes()
        for path in ASSETS.rglob("*")
        if path.is_file()
    }
    removed = json.loads((ROOT / "docs/removed-assets.json").read_text())
    forbidden = {entry["sha256"] for entry in removed}
    errors = inspect_release(records, register, forbidden)
    for entry in removed:
        if entry["path"] != "app/assets/images/og-preview.png" and (ROOT / entry["path"]).is_file():
            errors.append(f"Withdrawn path restored: {entry['path']}")
    for archive in args.archives:
        try:
            errors.extend(
                f"{archive.name}: {error}"
                for error in inspect_release(archive_records(archive), register, forbidden)
            )
        except (ValueError, OSError) as exc:
            errors.append(f"Cannot verify {archive}: {exc}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Verified {len(register)} individually recorded assets and {len(args.archives)} archives"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
