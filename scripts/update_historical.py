#!/usr/bin/env python3
"""CLI wrapper for the application's historical-results refresh service."""

import argparse
import asyncio

from app.services.historical_refresh import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update historical race results")
    parser.add_argument(
        "--circuit",
        type=str,
        default=None,
        help="Update only specific circuit (e.g., 'albert_park')",
    )

    args = parser.parse_args()
    asyncio.run(main(args.circuit))
