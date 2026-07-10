"""Tests for release readiness validation."""

from __future__ import annotations

import runpy
import subprocess

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


def test_validate_release_readiness_rejects_collapsible_html(tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        """# Changelog

## [Unreleased]

## [1.3.0] - 2026-03-14

### Backend

<details markdown="1">
<summary>Backend</summary>

- Added release notes

</details>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 9))

    with pytest.raises(ValueError, match="must not contain HTML details/summary blocks"):
        release_validation.validate_release_readiness(changelog)


def test_parse_latest_release_section_ignores_footer_links(monkeypatch):
    changelog = """# Changelog

## [Unreleased]

## [1.2.9] - 2026-03-13

[Unreleased]: https://example.com/compare/v1.2.9...HEAD
[1.2.9]: https://example.com/releases/tag/v1.2.9
"""
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 8))

    result = release_validation.parse_latest_release_section(changelog)

    assert result.release_body == ""


def test_parse_latest_release_section_requires_unreleased_heading(monkeypatch):
    changelog = """# Changelog

## [1.3.0] - 2026-03-14

- Added release notes
"""
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 9))

    with pytest.raises(ValueError, match="Missing required"):
        release_validation.parse_latest_release_section(changelog)


def test_parse_latest_release_section_requires_semantic_version():
    with pytest.raises(ValueError, match="No semantic version headings"):
        release_validation.parse_latest_release_section("# Changelog\n\n## [Unreleased]\n")


def test_get_latest_git_tag_requires_git(monkeypatch):
    monkeypatch.setattr(release_validation.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="git executable not found"):
        release_validation.get_latest_git_tag()


def test_get_latest_git_tag_wraps_command_failures(monkeypatch):
    monkeypatch.setattr(release_validation.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        release_validation.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "git")),
    )

    with pytest.raises(RuntimeError, match="Failed to read git tags"):
        release_validation.get_latest_git_tag()


@pytest.mark.parametrize(
    ("output", "expected"),
    [("invalid\nv1.2.3\n", SemVer(1, 2, 3)), ("invalid\n", None)],
)
def test_get_latest_git_tag_filters_non_semantic_tags(output, expected, monkeypatch):
    monkeypatch.setattr(release_validation.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(release_validation.subprocess, "check_output", lambda *_a, **_k: output)

    assert release_validation.get_latest_git_tag() == expected


def test_validate_release_readiness_rejects_empty_release_body(tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [1.3.0] - 2026-03-14\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_validation, "get_latest_git_tag", lambda: SemVer(1, 2, 9))

    with pytest.raises(ValueError, match="must not be empty"):
        release_validation.validate_release_readiness(changelog)


def test_release_validation_main_reports_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        release_validation,
        "validate_release_readiness",
        lambda: (_ for _ in ()).throw(ValueError("broken")),
    )

    assert release_validation.main() == 1
    assert "Release readiness check failed: broken" in capsys.readouterr().err


@pytest.mark.parametrize("latest_tag", [None, SemVer(1, 2, 9)])
def test_release_validation_main_reports_success(latest_tag, monkeypatch, capsys):
    result = release_validation.ReleaseValidationResult(
        latest_version=SemVer(1, 3, 0),
        latest_tag=latest_tag,
        unreleased_body="",
        release_body="- Ready",
    )
    monkeypatch.setattr(release_validation, "validate_release_readiness", lambda: result)

    assert release_validation.main() == 0
    output = capsys.readouterr().out
    assert "Release readiness OK" in output
    assert f"latest tag {latest_tag or 'none'}" in output


def test_release_validation_module_entrypoint():
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(release_validation.__file__), run_name="__main__")

    assert exc_info.value.code == 0
