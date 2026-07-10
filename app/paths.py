"""Stable source-checkout and installed-package resource paths."""

from pathlib import Path
from sysconfig import get_path

APP_PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_PACKAGE_DIR / "assets"
TEMPLATES_DIR = APP_PACKAGE_DIR / "templates"

_SOURCE_ROOT = APP_PACKAGE_DIR.parent
_INSTALLED_DATA_DIR = Path(get_path("data")) / "share" / "f1-eink-cal"


def _prefer_source(source_path: Path, installed_path: Path) -> Path:
    """Use checkout resources for development and wheel data after installation."""
    return source_path if source_path.exists() else installed_path


TRANSLATIONS_DIR = _prefer_source(
    _SOURCE_ROOT / "translations", _INSTALLED_DATA_DIR / "translations"
)
CHANGELOG_PATH = _prefer_source(_SOURCE_ROOT / "CHANGELOG.md", _INSTALLED_DATA_DIR / "CHANGELOG.md")
