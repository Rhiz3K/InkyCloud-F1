#!/usr/bin/env python3
"""CLI wrapper for the application's historical-results refresh service."""

import argparse
import asyncio

from app.services.historical_refresh import main
from app.services.http_client import close_shared_http_clients


async def run(circuit: str | None) -> None:
    """Run one refresh and release the shared client the service borrows."""
    try:
        await main(circuit)
    finally:
        await close_shared_http_clients()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update historical race results")
    parser.add_argument(
        "--circuit",
        type=str,
        default=None,
        help="Update only specific circuit (e.g., 'albert_park')",
    )

    args = parser.parse_args()
    asyncio.run(run(args.circuit))
