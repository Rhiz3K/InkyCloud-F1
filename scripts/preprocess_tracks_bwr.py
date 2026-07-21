#!/usr/bin/env python3
"""Compatibility wrapper for BWR track preprocessing."""

from scripts.manage import legacy_main

if __name__ == "__main__":
    legacy_main("tracks", "bwr")
