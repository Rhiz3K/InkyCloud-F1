"""Tests for release readiness validation."""

from __future__ import annotations

import pytest

from app.utils import release_validation
from app.utils.release_validation import SemVer


def test_parse_latest_release_section_reads_unreleased_and_version(monkeypatch):
    changelog = """# Changelog

## [Unreleased]

## [1.2.9] - 2026-03-13

- Added release notes

## [1.2.8] - 2026-03-11

- Older notes
"""
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 8))

    result = release_validation.parse_latest_release_section(changelog)

    assert result.latest_version == SemVer(1, 2, 9)
    assert result.latest_tag == SemVer(1, 2, 8)
    assert result.unreleased_body == ""
    assert result.release_body == "- Added release notes"


def test_validate_release_readiness_fails_when_unreleased_not_empty(tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        """# Changelog

## [Unreleased]

- Pending item

## [1.2.9] - 2026-03-13

- Added release notes
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 8))

    with pytest.raises(ValueError, match="Unreleased section must be empty"):
        release_validation.validate_release_readiness(changelog)


def test_validate_release_readiness_fails_when_version_not_bumped(tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        """# Changelog

## [Unreleased]

## [1.2.9] - 2026-03-13

- Added release notes
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 9))

    with pytest.raises(ValueError, match="Latest CHANGELOG version must be greater"):
        release_validation.validate_release_readiness(changelog)


def test_validate_release_readiness_passes_for_bumped_version(tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        """# Changelog

## [Unreleased]

## [1.3.0] - 2026-03-14

- Added release notes
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 9))

    result = release_validation.validate_release_readiness(changelog)

    assert result.latest_version == SemVer(1, 3, 0)
