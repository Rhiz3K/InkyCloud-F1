"""Read-only SQLite access that leaves no ``-wal``/``-shm`` files behind."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

_IMMUTABLE_SUFFIX = "&immutable=1"


def read_only_uri(path: Path, *, allow_immutable: bool = True) -> str:
    """Return a read-only URI for a database that may belong to a running service.

    A read-only connection to a WAL database creates ``-wal``/``-shm`` files it cannot remove
    afterwards; left behind, they make maintenance tools think the service still runs. SQLite
    keeps the ``-wal`` file while any connection is open, so without one no writer is active
    and the file can be read as immutable, without locks or sidecars. Otherwise the existing
    sidecars are shared normally and the read sees a consistent snapshot. Immutable reads skip
    SQLite's locks, so use :func:`read_consistently` to catch a writer that starts meanwhile.
    """
    resolved = path.resolve(strict=True)
    if not allow_immutable or Path(f"{resolved}-wal").exists():
        return f"{resolved.as_uri()}?mode=ro"
    return f"{resolved.as_uri()}?mode=ro{_IMMUTABLE_SUFFIX}"


def database_state(path: Path) -> tuple[bool, int, int, int]:
    """Return what a writer changes: the ``-wal`` file's presence and the file's identity.

    A writer opening the database creates the ``-wal`` file before it changes anything, and
    any write that reaches the main file (a checkpoint) changes its size or modification time.
    """
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return Path(f"{resolved}-wal").exists(), stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_consistently(path: Path, read: Callable[[str], T]) -> T:
    """Run ``read`` on a read-only URI; repeat it under SQLite's locks if a writer interfered.

    An immutable read is only consistent when no other connection opens the database while it
    runs (a backup overlapping service startup, for example). When the database changed during
    such a read, or the read failed while it changed, it is repeated with a normal locked
    read-only connection, which may leave ``-wal``/``-shm`` files behind but is consistent.
    """
    before = database_state(path)
    uri = read_only_uri(path)
    if not uri.endswith(_IMMUTABLE_SUFFIX):
        return read(uri)
    try:
        result = read(uri)
    except sqlite3.Error:
        if database_state(path) == before:
            raise
        return read(read_only_uri(path, allow_immutable=False))
    if database_state(path) != before:
        return read(read_only_uri(path, allow_immutable=False))
    return result
