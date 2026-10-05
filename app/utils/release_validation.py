"""Helpers for validating release readiness from CHANGELOG.md."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
UNRELEASED_HEADING = "## [Unreleased]"
VERSION_HEADING_RE = re.compile(r"^## \[(\d+)\.(\d+)\.(\d+)\] - .*$", re.MULTILINE)
FOOTER_LINK_RE = re.compile(r"^\[[^\]]+\]:\s*\S+", re.MULTILINE)
COLLAPSIBLE_HTML_RE = re.compile(r"<details\b|</details>|<summary\b", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class SemVer:
    """Comparable semantic-version triplet used by release validation."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        """Format the semantic version without a leading tag prefix."""
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ReleaseValidationResult:
    """Parsed release metadata and changelog bodies used by the merge gate."""

    latest_version: SemVer
    latest_tag: SemVer | None
    unreleased_body: str
    release_body: str

    @property
    def is_new_release(self) -> bool:
        """Return whether the newest CHANGELOG version has not been tagged yet."""
        return self.latest_tag is None or self.latest_version > self.latest_tag


def parse_latest_release_section(changelog: str) -> ReleaseValidationResult:
    """Parse the newest semantic release and Unreleased bodies from a changelog."""
    matches = list(VERSION_HEADING_RE.finditer(changelog))
    if not matches:
        raise ValueError("No semantic version headings found in CHANGELOG.md")

    first_match = matches[0]
    latest_version = SemVer(*(int(part) for part in first_match.groups()))

    unreleased_body = ""
    unreleased_index = changelog.find(UNRELEASED_HEADING)
    if unreleased_index == -1 or unreleased_index > first_match.start():
        raise ValueError(f"Missing required {UNRELEASED_HEADING} heading before latest release")
    unreleased_body = changelog[
        unreleased_index + len(UNRELEASED_HEADING) : first_match.start()
    ].strip()

    next_match = matches[1] if len(matches) > 1 else None
    release_end = next_match.start() if next_match else len(changelog)
    release_body = changelog[first_match.end() : release_end]
    footer_match = FOOTER_LINK_RE.search(release_body)
    if footer_match:
        release_body = release_body[: footer_match.start()]
    release_body = release_body.strip()

    return ReleaseValidationResult(
        latest_version=latest_version,
        latest_tag=get_latest_git_tag(),
        unreleased_body=unreleased_body,
        release_body=release_body,
    )


def get_latest_git_tag() -> SemVer | None:
    """Return the newest semantic ``v*`` Git tag, if one exists."""
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable not found")

    try:
        output = subprocess.check_output(
            [git_executable, "tag", "--list", "v*", "--sort=-v:refname"],
            text=True,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Failed to read git tags") from exc

    for line in output:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", line.strip())
        if match:
            return SemVer(*(int(part) for part in match.groups()))

    return None


def _has_changelog_entries(body: str) -> bool:
    """Return whether a changelog section body contains more than headings."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in body.splitlines())


def validate_release_readiness(changelog_path: Path = CHANGELOG_PATH) -> ReleaseValidationResult:
    """Validate that a main PR either records Unreleased notes or prepares a new release.

    A PR that keeps the newest version equal to the latest tag defers its release and must
    describe the change under Unreleased. A PR that adds a newer version publishes it on merge,
    so its notes must move into that release section and Unreleased must be empty.
    """
    changelog = changelog_path.read_text(encoding="utf-8")
    result = parse_latest_release_section(changelog)

    if COLLAPSIBLE_HTML_RE.search(changelog):
        raise ValueError("CHANGELOG must not contain HTML details/summary blocks")

    if result.latest_tag is not None and result.latest_version < result.latest_tag:
        raise ValueError(
            "Latest CHANGELOG version must not be older than the latest git tag: "
            f"{result.latest_version} < {result.latest_tag}"
        )

    if not result.is_new_release:
        if not _has_changelog_entries(result.unreleased_body):
            raise ValueError(
                f"Describe the change under {UNRELEASED_HEADING} or add a release section "
                f"newer than {result.latest_version}"
            )
        return result

    if result.unreleased_body:
        raise ValueError("Unreleased section must be empty before merging a release PR to main")

    if not result.release_body:
        raise ValueError(f"Release section {result.latest_version} must not be empty")

    return result


def main() -> int:
    """Run release validation as a command-line merge gate."""
    try:
        result = validate_release_readiness()
    except Exception as exc:
        print(f"Release readiness check failed: {exc}", file=sys.stderr)
        return 1

    latest_tag = str(result.latest_tag) if result.latest_tag is not None else "none"
    if not result.is_new_release:
        print(
            "Changelog OK: Unreleased notes recorded, "
            f"no new release (CHANGELOG {result.latest_version}, latest tag {latest_tag})"
        )
        return 0

    print(
        "Release readiness OK: "
        f"CHANGELOG {result.latest_version}, latest tag {latest_tag}, "
        f"release body length {len(result.release_body)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
