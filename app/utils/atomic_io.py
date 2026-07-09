"""Atomic file-writing helpers shared by runtime and maintenance jobs."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import aiofiles


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


async def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace *path* with byte data written in the same directory."""
    tmp_path = _temporary_path(path)
    try:
        async with aiofiles.open(tmp_path, "wb") as handle:
            await handle.write(data)
        os.replace(tmp_path, path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def atomic_write_bytes_sync(path: Path, data: bytes) -> None:
    """Synchronously replace *path* with byte data written in the same directory."""
    tmp_path = _temporary_path(path)
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def atomic_save_image(path: Path, image: Any, *, format: str) -> None:
    """Save a Pillow-compatible image to a temporary file and atomically replace *path*."""
    tmp_path = _temporary_path(path)
    try:
        image.save(tmp_path, format=format)
        os.replace(tmp_path, path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically write indented UTF-8 JSON with a trailing newline."""
    tmp_path = _temporary_path(path)
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
