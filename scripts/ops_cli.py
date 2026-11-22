#!/usr/bin/env python3
"""
Operator diagnostics for F1 E-Ink Calendar.

Usage:
    ops status [--json]  - Readiness, degraded reason codes, weather quota, backups and profile
"""

import sys

from app.services.ops_status import main

if __name__ == "__main__":
    sys.exit(main())
