"""Strong ETag and pregenerated-image sidecar helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_STRONG_ETAG_RE = re.compile(r'^"[0-9a-f]{64}"$')


def strong_etag(content: bytes) -> str:
    """Return a quoted strong SHA-256 ETag for exact response bytes."""
    return f'"{hashlib.sha256(content).hexdigest()}"'


def etag_sidecar_path(image_path: Path) -> Path:
    """Return the mtime-bound ETag sidecar path for a BMP artifact."""
    return image_path.with_suffix(f"{image_path.suffix}.etag")


def encode_etag_sidecar(image_mtime_ns: int, etag: str) -> bytes:
    """Encode the BMP mtime and ETag used to validate a sidecar without reading the BMP."""
    if not _STRONG_ETAG_RE.fullmatch(etag):
        raise ValueError("sidecar requires a strong SHA-256 ETag")
    return f"{image_mtime_ns}\n{etag}\n".encode("ascii")


def read_etag_sidecar(image_path: Path) -> str | None:
    """Read a valid sidecar whose recorded mtime still matches the BMP."""
    try:
        lines = etag_sidecar_path(image_path).read_text(encoding="ascii").splitlines()
        if len(lines) != 2:
            return None
        recorded_mtime_ns = int(lines[0])
        etag = lines[1]
        if image_path.stat().st_mtime_ns != recorded_mtime_ns:
            return None
    except OSError, ValueError:
        return None
    return etag if _STRONG_ETAG_RE.fullmatch(etag) else None


def if_none_match_matches(header_value: str | None, etag: str) -> bool:
    """Return whether an If-None-Match list contains the current entity tag."""
    if not header_value:
        return False
    for candidate in header_value.split(","):
        normalized = candidate.strip()
        if normalized == "*" or normalized.removeprefix("W/") == etag:
            return True
    return False
