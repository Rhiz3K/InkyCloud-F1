#!/usr/bin/env python3
"""Compatibility wrapper for Spectra 6 track preprocessing."""

import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.manage import legacy_main  # noqa: E402

if __name__ == "__main__":
    legacy_main("tracks", "spectra6")
