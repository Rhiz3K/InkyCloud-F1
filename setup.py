"""Legacy setup.py shim delegating to ``pyproject.toml`` metadata."""

import tomllib
from pathlib import Path

from setuptools import setup


def _load_project_metadata() -> dict:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    setuptools_cfg = pyproject.get("tool", {}).get("setuptools", {})

    return {
        "name": project["name"],
        "version": project["version"],
        "description": project.get("description", ""),
        "long_description": Path(project.get("readme", "README.md")).read_text(encoding="utf-8"),
        "long_description_content_type": "text/markdown",
        "python_requires": project.get("requires-python"),
        "install_requires": project.get("dependencies", []),
        "packages": setuptools_cfg.get("packages", []),
        "include_package_data": setuptools_cfg.get("include-package-data", False),
        "package_data": setuptools_cfg.get("package-data", {}),
    }


setup(**_load_project_metadata())
