"""Process-local caches and buffered request-statistics state."""

import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from cachetools import TTLCache

from app.config import config
from app.services.private_stats import (
    DIMENSIONS,
    accumulate,
    normalize_call,
    normalize_track_options,
)

logger = logging.getLogger(__name__)

BMP_CACHE_MAXSIZE = 512
BMP_CACHE_TTL_SECONDS = 3600
BmpArtifact = tuple[bytes, str]


class ImageCache(TTLCache[str, BmpArtifact]):
    """A BMP cache that exposes the remaining lifetime of a selected image."""

    def __init__(self, maxsize: int, ttl: int, timer: Callable[[], float] = time.monotonic):
        """Use the same monotonic clock for admission, expiry and refresh hints."""
        super().__init__(maxsize, ttl, timer=timer)
        self._deadlines: dict[str, float] = {}

    def __setitem__(self, key: str, value: BmpArtifact) -> None:
        """Remember the exact deadline assigned by TTLCache, including replacements."""
        with self.timer as now:
            super().__setitem__(key, value)
            self._deadlines[key] = now + self.ttl

    def __delitem__(self, key: str) -> None:
        """Drop the deadline when an entry is removed or evicted."""
        self._deadlines.pop(key, None)
        super().__delitem__(key)

    def expire(self, time: float | None = None) -> list[tuple[str, BmpArtifact]]:
        """Discard deadlines alongside expired images."""
        expired = super().expire(time)
        for key, _ in expired:
            self._deadlines.pop(key, None)
        return expired

    def clear(self) -> None:
        """Clear the image deadlines along with their bytes."""
        super().clear()
        self._deadlines.clear()

    def remaining_seconds(self, key: str) -> float:
        """Return the remaining cache lifetime, or zero for an expired or evicted key."""
        now = self.timer()
        return max(0.0, self._deadlines.get(key, now) - now)


# Cover all localized display/weather variants without immediate LRU churn.
# Teams extra panels and extra previews live in a second cache so a burst of those
# variants cannot evict the calendar and plain teams entries stored here.
_bmp_cache: ImageCache = ImageCache(
    maxsize=BMP_CACHE_MAXSIZE,
    ttl=BMP_CACHE_TTL_SECONDS,
)


class _VariantCacheSlot:
    """Holds the live extra/preview cache so a resize can replace it."""

    def __init__(self, cache: ImageCache) -> None:
        """Store the cache currently serving extra and preview images."""
        self.cache = cache


_variant_cache_slot = _VariantCacheSlot(
    ImageCache(maxsize=config.BMP_VARIANT_CACHE_MAXSIZE, ttl=BMP_CACHE_TTL_SECONDS)
)

# Bound the buffer so a serving instance whose flush job never runs (e.g. SCHEDULER_ENABLED
# is false) cannot grow it without limit. deque(maxlen) enforces the cap structurally:
# appends past the cap drop the oldest entry, so no mutation site can forget to trim.
API_CALLS_BUFFER_MAXSIZE = 10_000
_api_calls_buffer: deque = deque(maxlen=API_CALLS_BUFFER_MAXSIZE)
_aggregate_calls: OrderedDict[tuple, dict] = OrderedDict()


class _CacheGeneration:
    """Counts cache clears so in-flight renders can tell their inputs went stale."""

    value = 0


def clear_bmp_cache() -> None:
    """Remove generated images from the main cache and the extra/preview cache."""
    _CacheGeneration.value += 1
    _bmp_cache.clear()
    _variant_cache_slot.cache.clear()
    logger.info("BMP cache cleared")


def bmp_cache_generation() -> int:
    """Return a token that changes whenever the image caches are cleared.

    A render captures it before loading data and stores its result only if the token is
    unchanged, so bytes built from pre-refresh data cannot repopulate a freshly cleared cache.
    """
    return _CacheGeneration.value


def get_bmp_cache() -> ImageCache:
    """Return the cache of calendar images and plain teams dashboards."""
    return _bmp_cache


def get_bmp_variant_cache() -> ImageCache:
    """Return the cache of teams extra panels and extra preview images.

    The configured maximum is applied on read. Shrinking keeps the newest entries
    and drops the rest. The time-to-live stays aligned with the main cache.
    """
    maxsize = config.BMP_VARIANT_CACHE_MAXSIZE
    current = _variant_cache_slot.cache
    if current.maxsize == maxsize:
        return current
    retained = list(current.items())[-maxsize:]
    rebuilt: ImageCache = ImageCache(maxsize=maxsize, ttl=BMP_CACHE_TTL_SECONDS)
    for key, value in retained:
        rebuilt[key] = value
    _variant_cache_slot.cache = rebuilt
    return rebuilt


def record_api_call(
    endpoint: str,
    response_time_ms: float,
    response_size_bytes: int,
    lang: str | None = None,
    tz: str | None = None,
    year: int | None = None,
    round_num: int | None = None,
    race_name: str | None = None,
    is_auto_selected: bool = False,
    display_type: str | None = None,
    status_code: int = 200,
    *,
    track_style: str | None = None,
    track_source: str | None = None,
    track_accent: str | None = None,
) -> None:
    """Append one normalized API-call record to the bounded flush buffer."""
    if config.MINIMAL_DATA_MODE:
        return
    call = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "response_time_ms": response_time_ms,
        "response_size_bytes": response_size_bytes,
        "lang": lang,
        "tz": tz,
        "year": year,
        "round": round_num,
        "race_name": race_name,
        "is_auto_selected": 1 if is_auto_selected else 0,
        "display_type": display_type,
        "status_code": status_code,
        **normalize_track_options(
            {
                "endpoint": endpoint,
                "track_style": track_style,
                "track_source": track_source,
                "track_accent": track_accent,
            }
        ),
    }
    if config.AGGREGATE_STATS_ONLY:
        bucket = normalize_call(call)
        key = tuple(bucket[field] for field in DIMENSIONS)
        if key in _aggregate_calls:
            accumulate(_aggregate_calls[key], bucket)
        else:
            _aggregate_calls[key] = bucket
            if len(_aggregate_calls) > API_CALLS_BUFFER_MAXSIZE:
                _aggregate_calls.popitem(last=False)
    else:
        call["event_id"] = str(uuid4())
        _api_calls_buffer.append(call)


def get_and_clear_api_calls_buffer() -> list:
    """Drain and return all currently buffered API-call records."""
    if config.MINIMAL_DATA_MODE:
        _api_calls_buffer.clear()
        _aggregate_calls.clear()
        return []
    calls = list(_api_calls_buffer) + list(_aggregate_calls.values())
    if _aggregate_calls:
        batch_id = str(uuid4())
        for bucket in _aggregate_calls.values():
            bucket["batch_id"] = batch_id
    _api_calls_buffer.clear()
    _aggregate_calls.clear()
    return calls


def requeue_api_calls(calls: list) -> None:
    """Put calls back at the front of the buffer after a failed flush.

    Rebuilds in chronological order (requeued calls are older than anything buffered since);
    deque.extend with maxlen then drops the oldest overflow. extendleft on a full deque would
    instead evict from the right — the newest records — which is the wrong end to lose.
    """
    if config.MINIMAL_DATA_MODE:
        _api_calls_buffer.clear()
        _aggregate_calls.clear()
        return
    if not calls:
        return
    combined = list(calls) + list(_api_calls_buffer)
    _api_calls_buffer.clear()
    _api_calls_buffer.extend(combined)
