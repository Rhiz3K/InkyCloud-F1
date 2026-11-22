"""Read-only SQLite access that leaves no ``-wal``/``-shm`` files behind."""

from __future__ import annotations

from pathlib import Path


def read_only_uri(path: Path) -> str:
    """Return a read-only URI for a database that may belong to a running service.

    A read-only connection to a WAL database creates ``-wal``/``-shm`` files it cannot remove
    afterwards; left behind, they make maintenance tools think the service still runs. SQLite
    keeps the ``-wal`` file while any connection is open, so without one no writer is active
    and the file can be read as immutable, without locks or sidecars. Otherwise the existing
    sidecars are shared normally and the read sees a consistent snapshot.
    """
    resolved = path.resolve(strict=True)
    if Path(f"{resolved}-wal").exists():
        return f"{resolved.as_uri()}?mode=ro"
    return f"{resolved.as_uri()}?mode=ro&immutable=1"
