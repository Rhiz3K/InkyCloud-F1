"""Remote track asset client with local disk cache and local fallback support."""

from __future__ import annotations

import io
import json
import logging
from functools import lru_cache
from pathlib import Path
from time import time
from typing import Any, Iterable

import httpx
from PIL import Image

from app.config import get_config

logger = logging.getLogger(__name__)


class AssetClient:
    """Fetches remote track assets from the internal asset API."""

    def __init__(self) -> None:
        config = get_config()
        self.base_url = (config.ASSET_API_URL or "").rstrip("/")
        self.token = config.ASSET_API_TOKEN
        self.timeout = config.REQUEST_TIMEOUT
        self.cache_dir = Path(config.ASSET_CACHE_DIR)
        self.cache_ttl_seconds = config.ASSET_CACHE_TTL_HOURS * 3600

    def is_enabled(self) -> bool:
        """Return True when remote asset fetching is configured."""
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _binary_headers(self) -> dict[str, str]:
        headers = {"Accept": "image/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _normalize_ids(self, circuit_ids: str | Iterable[str]) -> list[str]:
        if isinstance(circuit_ids, str):
            candidates = [circuit_ids]
        else:
            candidates = list(circuit_ids)

        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate:
                continue
            value = candidate.strip()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return normalized

    def _manifest_cache_path(self, circuit_id: str) -> Path:
        return self.cache_dir / "manifests" / f"{circuit_id}.json"

    def _binary_cache_path(self, circuit_id: str, variant: str) -> Path:
        return self.cache_dir / "tracks" / variant / f"{circuit_id}.bmp"

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_seconds = time() - path.stat().st_mtime
        return age_seconds <= self.cache_ttl_seconds

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def get_manifest(self, circuit_id: str) -> dict[str, Any] | None:
        """Fetch and cache manifest metadata for a circuit."""
        if not self.is_enabled():
            return None

        cache_path = self._manifest_cache_path(circuit_id)
        if self._cache_is_fresh(cache_path):
            cached = self._read_json(cache_path)
            if cached is not None:
                return cached

        url = f"{self.base_url}/v1/tracks/{circuit_id}/manifest"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(response.text)
            return response.json()
        except Exception as exc:
            logger.debug("Failed to fetch track manifest for %s: %s", circuit_id, exc)
            return self._read_json(cache_path) if cache_path.exists() else None

    def _fetch_binary_bytes(self, circuit_id: str, variant: str) -> bytes | None:
        cache_path = self._binary_cache_path(circuit_id, variant)
        if self._cache_is_fresh(cache_path):
            try:
                return cache_path.read_bytes()
            except OSError:
                pass

        url = f"{self.base_url}/v1/tracks/{circuit_id}/binary"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(
                    url,
                    params={"variant": variant},
                    headers=self._binary_headers(),
                )
                response.raise_for_status()
            payload = response.content
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
            return payload
        except Exception as exc:
            logger.debug(
                "Failed to fetch remote track binary for %s (%s): %s",
                circuit_id,
                variant,
                exc,
            )
            if cache_path.exists():
                try:
                    return cache_path.read_bytes()
                except OSError:
                    return None
            return None

    def get_track_image(self, circuit_ids: str | Iterable[str], variant: str) -> Image.Image | None:
        """Try candidate circuit ids until a remote track asset is found."""
        if not self.is_enabled():
            return None

        for circuit_id in self._normalize_ids(circuit_ids):
            manifest = self.get_manifest(circuit_id)
            if manifest is not None:
                variants = manifest.get("variants") or {}
                if variant not in variants:
                    continue

            payload = self._fetch_binary_bytes(circuit_id, variant)
            if payload is None:
                continue

            try:
                image = Image.open(io.BytesIO(payload))
                image.load()
                return image
            except Exception as exc:
                logger.warning(
                    "Failed to decode remote track asset for %s (%s): %s",
                    circuit_id,
                    variant,
                    exc,
                )

        return None


@lru_cache()
def get_asset_client() -> AssetClient:
    """Build the asset client once per process."""
    return AssetClient()


def _reset_asset_client_cache_for_tests() -> None:
    """Allow tests to rebuild the cached asset client after env changes."""
    get_asset_client.cache_clear()
