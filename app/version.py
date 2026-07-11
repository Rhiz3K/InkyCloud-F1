"""Application release metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("f1-eink-cal")
except PackageNotFoundError:  # pragma: no cover - only raw, uninstalled source trees
    APP_VERSION = "0.0.0+unknown"
