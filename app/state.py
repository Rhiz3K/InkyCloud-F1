import logging
from datetime import datetime, timezone

from cachetools import TTLCache

logger = logging.getLogger(__name__)

_bmp_cache: TTLCache = TTLCache(maxsize=100, ttl=3600)

_api_calls_buffer: list = []


def clear_bmp_cache() -> None:
    _bmp_cache.clear()
    logger.info("BMP cache cleared")


def get_bmp_cache() -> TTLCache:
    return _bmp_cache


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
) -> None:
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
    }
    _api_calls_buffer.append(call)


def get_and_clear_api_calls_buffer() -> list:
    calls = _api_calls_buffer.copy()
    _api_calls_buffer.clear()
    return calls
