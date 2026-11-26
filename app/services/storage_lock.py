"""Process-lifetime coordination for the database and its mutable circuit files."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def storage_lock(database: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Hold a shared service lock or exclusive restore lock on a stable sibling inode."""
    database.parent.mkdir(parents=True, exist_ok=True)
    path = database.with_suffix(database.suffix + ".lock")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDONLY, 0o644)
    except PermissionError as exc:
        raise PermissionError(f"Cannot open storage lock {path} for reading") from exc
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)
