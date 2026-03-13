"""Helpers for validating release readiness from CHANGELOG.md."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
UNRELEASED_HEADING = "## [Unreleased]"
VERSION_HEADING_RE = re.compile(r"^## \[(\d+)\.(\d+)\.(\d+)\] - .*$", re.MULTILINE)


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ReleaseValidationResult:
    latest_version: SemVer
    latest_tag: SemVer | None
    unreleased_body: str
    release_body: str


def parse_latest_release_section(changelog: str) -> ReleaseValidationResult:
    matches = list(VERSION_HEADING_RE.finditer(changelog))
    if not matches:
        raise ValueError("No semantic version headings found in CHANGELOG.md")

    first_match = matches[0]
    latest_version = SemVer(*(int(part) for part in first_match.groups()))

    unreleased_body = ""
    unreleased_index = changelog.find(UNRELEASED_HEADING)
    if unreleased_index != -1 and unreleased_index < first_match.start():
        unreleased_body = changelog[
            unreleased_index + len(UNRELEASED_HEADING) : first_match.start()
        ].strip()

    next_match = matches[1] if len(matches) > 1 else None
    release_body = changelog[
        first_match.end() : next_match.start() if next_match else len(changelog)
    ].strip()

    return ReleaseValidationResult(
        latest_version=latest_version,
        latest_tag=get_latest_git_tag(),
        unreleased_body=unreleased_body,
        release_body=release_body,
    )


def get_latest_git_tag() -> SemVer | None:
    try:
        output = subprocess.check_output(
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            text=True,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Failed to read git tags") from exc

    for line in output:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", line.strip())
        if match:
            return SemVer(*(int(part) for part in match.groups()))

    return None


def validate_release_readiness(changelog_path: Path = CHANGELOG_PATH) -> ReleaseValidationResult:
    changelog = changelog_path.read_text(encoding="utf-8")
    result = parse_latest_release_section(changelog)

    if result.unreleased_body:
        raise ValueError("Unreleased section must be empty before merging a release PR to main")

    if not result.release_body:
        raise ValueError(f"Release section {result.latest_version} must not be empty")

    if result.latest_tag is not None and result.latest_version <= result.latest_tag:
        raise ValueError(
            "Latest CHANGELOG version must be greater than the latest git tag: "
            f"{result.latest_version} <= {result.latest_tag}"
        )

    return result


def main() -> int:
    try:
        result = validate_release_readiness()
    except Exception as exc:
        print(f"Release readiness check failed: {exc}", file=sys.stderr)
        return 1

    latest_tag = str(result.latest_tag) if result.latest_tag is not None else "none"
    print(
        "Release readiness OK: "
        f"CHANGELOG {result.latest_version}, latest tag {latest_tag}, "
        f"release body length {len(result.release_body)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
