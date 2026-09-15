"""Serve only individually recorded public assets with their reviewed bytes."""

from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"


@lru_cache(maxsize=4)
def read_asset_register(path: str, _mtime_ns: int) -> dict:
    """Cache the reviewed register, invalidating it when the manifest changes."""
    return json.loads(Path(path).read_text(encoding="utf-8"))["assets"]


@lru_cache(maxsize=512)
def asset_digest(path: str, _mtime_ns: int, _size: int) -> str:
    """Hash exact asset bytes once per immutable filesystem version."""
    return sha256(Path(path).read_bytes()).hexdigest()


class LicensedStaticFiles(StaticFiles):
    """Deny unregistered files, old installed leftovers and altered asset bytes."""

    async def get_response(self, path: str, scope):
        """Check the public allowlist before Starlette resolves the file response."""
        root = Path(str(self.directory)).resolve()
        register_path = root / "asset-register.json"
        try:
            register = read_asset_register(str(register_path), register_path.stat().st_mtime_ns)
            source = root / path
            entry = register.get(path)
            resolved = source.resolve(strict=True)
            if not entry or not resolved.is_relative_to(root) or source.is_symlink():
                raise HTTPException(404)
            stat = resolved.stat()
            if asset_digest(str(resolved), stat.st_mtime_ns, stat.st_size) != entry["sha256"]:
                raise HTTPException(404)
        except OSError, KeyError, ValueError, TypeError:
            raise HTTPException(404) from None
        return await super().get_response(path, scope)
