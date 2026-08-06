#!/usr/bin/env python3
"""CLI wrapper for the application's historical-results refresh service."""

import argparse
import asyncio

from app.services.historical_refresh import main
from app.services.http_client import close_shared_http_clients


async def run(circuit: str | None) -> None:
    """Run one refresh and release the shared client the service borrows."""
    refresh_error: BaseException | None = None
    try:
        await main(circuit)
    except BaseException as error:
        refresh_error = error
        raise
    finally:
        try:
            await close_shared_http_clients()
        except BaseException as cleanup_error:
            if refresh_error is None:
                raise
            refresh_error.add_note(f"Shared HTTP client cleanup also failed: {cleanup_error!r}")


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
