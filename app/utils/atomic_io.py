"""Atomic file-writing helpers shared by runtime and maintenance jobs."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any


def _temporary_path(path: Path) -> Path:
    """Return a unique temporary sibling path for an atomic replacement."""
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


async def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace *path* with byte data written in the same directory."""
    import aiofiles

    tmp_path = _temporary_path(path)
    try:
        async with aiofiles.open(tmp_path, "wb") as handle:
            await handle.write(data)
            await handle.flush()
        await asyncio.to_thread(_fsync_file, tmp_path)
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def atomic_write_bytes_sync(path: Path, data: bytes) -> None:
    """Synchronously replace *path* with byte data written in the same directory."""
    tmp_path = _temporary_path(path)
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def atomic_save_image(path: Path, image: Any, *, image_format: str) -> None:
    """Save a Pillow-compatible image to a temporary file and atomically replace *path*."""
    tmp_path = _temporary_path(path)
    try:
        image.save(tmp_path, format=image_format)
        with tmp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def _fsync_directory(directory: Path) -> None:
    """Persist a rename on filesystems that require syncing the parent directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            return
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    """Flush an already-written file to stable storage."""
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
