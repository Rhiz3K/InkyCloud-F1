"""Edge-case coverage for shared utility modules."""

from __future__ import annotations

import asyncio
import random
import weakref
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import Request
from PIL import Image

from app.services import http_client
from app.utils import async_locks, async_tasks, atomic_io, jolpica, rate_limit
from app.utils import http as http_utils
from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette
from app.utils.f1_season import SEASON_START_DATES, get_current_f1_season
from app.utils.http import _retry_after_seconds, fetch_with_retry
from app.utils.image_assets import decode_image_bytes
from app.utils.race_times import convert_race_times_to_timezone
from app.utils.result_entries import parse_result_position, sort_entries_by_position
from app.web.templates import _build_configure_ui_text, calc_percent, detect_ui_language


def _request(*, client: tuple[str, int] | None = ("127.0.0.1", 1234), cookies: str = "") -> Request:
    headers = [(b"cookie", cookies.encode())] if cookies else []
    return Request({"type": "http", "client": client, "headers": headers})


@pytest.mark.asyncio
async def test_close_shared_http_clients_skips_objects_without_aclose():
    http_client._shared_http_clients[object()] = object()

    await http_client.close_shared_http_clients()

    assert not http_client._shared_http_clients


def test_shutdown_render_executor_is_noop_before_initialization():
    async_tasks._get_render_executor.cache_clear()

    async_tasks.shutdown_render_executor()

    assert async_tasks._get_render_executor.cache_info().currsize == 0


def test_atomic_save_image_writes_and_syncs_image(tmp_path):
    output = tmp_path / "calendar.png"

    atomic_io.atomic_save_image(output, Image.new("RGB", (2, 2), "white"), image_format="PNG")

    with Image.open(output) as image:
        assert image.size == (2, 2)


def test_fsync_directory_ignores_open_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_io.os, "open", MagicMock(side_effect=OSError("unsupported")))

    atomic_io._fsync_directory(tmp_path)


def test_quantize_to_palette_uses_requested_colors():
    image = Image.new("RGB", (2, 1), "red")

    result = quantize_to_palette(image, [(0, 0, 0), (255, 0, 0)], 2)

    assert result.mode == "P"
    assert result.getpixel((0, 0)) in {0, 1}


def test_encode_indexed_bmp_rejects_non_palette_mode():
    with pytest.raises(ValueError, match="Expected image mode"):
        encode_indexed_bmp_4bit(Image.new("RGB", (1, 1)), [(0, 0, 0)])


def test_encode_indexed_bmp_rejects_oversized_palette():
    with pytest.raises(ValueError, match="max 16"):
        encode_indexed_bmp_4bit(Image.new("P", (1, 1)), [(0, 0, 0)] * 17)


def test_encode_indexed_bmp_rejects_missing_pixel_access():
    indexed = MagicMock(mode="P", size=(1, 1))
    indexed.tobytes.return_value = b""

    with pytest.raises(ValueError, match="Failed to access"):
        encode_indexed_bmp_4bit(indexed, [(0, 0, 0)])


@pytest.mark.parametrize(
    ("size", "bad_pixel", "message"),
    [
        ((1, 1), (0, 0), r"\(0, 0\): 16"),
        ((3, 2), (1, 1), r"\(1, 1\): 16"),
    ],
)
def test_encode_indexed_bmp_rejects_out_of_range_pixels(size, bad_pixel, message):
    indexed = Image.new("P", size, 0)
    indexed.putpixel(bad_pixel, 16)

    with pytest.raises(ValueError, match=message):
        encode_indexed_bmp_4bit(indexed, [(0, 0, 0)])


def _reference_encode_rows(indexed: Image.Image) -> bytes:
    width, height = indexed.size
    row_stride = ((((width + 1) // 2) + 3) // 4) * 4
    rows = []
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(0, width, 2):
            left = indexed.getpixel((x, y))
            right = indexed.getpixel((x + 1, y)) if x + 1 < width else 0
            row.append((left << 4) | right)
        row.extend(b"\x00" * (row_stride - len(row)))
        rows.append(bytes(row))
    return b"".join(rows)


@pytest.mark.parametrize("size", [(1, 1), (5, 3), (8, 2), (13, 7)])
def test_encode_indexed_bmp_packs_pixels_like_the_per_pixel_reference(size):
    rng = random.Random(size[0] * 100 + size[1])
    indexed = Image.new("P", size)
    indexed.putdata([rng.randrange(16) for _ in range(size[0] * size[1])])
    palette = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0)]

    encoded = encode_indexed_bmp_4bit(indexed, palette)

    pixel_offset = 14 + 40 + 16 * 4
    assert encoded[pixel_offset:] == _reference_encode_rows(indexed)
    with Image.open(BytesIO(encoded)) as decoded:
        assert decoded.size == size
        assert decoded.tobytes() == indexed.tobytes()


@pytest.mark.asyncio
async def test_get_keyed_loop_lock_reuses_locks_per_key():
    registry: async_locks.KeyedLoopLockRegistry = weakref.WeakKeyDictionary()

    first = async_locks.get_keyed_loop_lock(registry, ("season", 2026))

    assert async_locks.get_keyed_loop_lock(registry, ("season", 2026)) is first
    assert async_locks.get_keyed_loop_lock(registry, ("season", 2027)) is not first


def test_encode_indexed_bmp_supports_odd_width_and_empty_palette():
    indexed = Image.new("P", (1, 1))
    indexed.putpixel((0, 0), 0)

    assert encode_indexed_bmp_4bit(indexed, []).startswith(b"BM")


def test_current_f1_season_before_first_known_start():
    earliest = min(SEASON_START_DATES.values())
    before_first = datetime(earliest.year - 1, 1, 1, tzinfo=timezone.utc)

    assert get_current_f1_season(before_first) == min(SEASON_START_DATES) - 1


def test_season_start_dates_cover_current_calendar_year():
    current_year = datetime.now(timezone.utc).year

    assert max(SEASON_START_DATES) >= current_year, (
        f"SEASON_START_DATES must include the {current_year} first-race date"
    )


@pytest.mark.asyncio
async def test_jolpica_pacer_is_shared_by_host_within_an_event_loop():
    jolpica._reset_jolpica_pacers_for_tests()
    try:
        first = jolpica.get_jolpica_pacer("https://api.jolpi.ca/ergast/f1/current.json")
        same_host = jolpica.get_jolpica_pacer(
            "https://api.jolpi.ca/ergast/f1/2026/driverStandings.json"
        )
        other_host = jolpica.get_jolpica_pacer("https://mirror.example/ergast/f1")

        assert first is same_host
        assert first is not other_host
    finally:
        jolpica._reset_jolpica_pacers_for_tests()


@pytest.mark.asyncio
async def test_jolpica_pacer_keeps_full_teams_cache_miss_concurrent():
    jolpica._reset_jolpica_pacers_for_tests()
    try:
        pacer = jolpica.get_jolpica_pacer()
        with patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as sleep:
            await asyncio.gather(*(pacer.wait() for _ in range(4)))
            await asyncio.gather(*(pacer.wait() for _ in range(2)))

        sleep.assert_not_awaited()
    finally:
        jolpica._reset_jolpica_pacers_for_tests()


def test_retry_after_rejects_invalid_http_date():
    response = httpx.Response(429, headers={"Retry-After": "not-a-date"})

    assert _retry_after_seconds(response) is None


@pytest.mark.parametrize("aware", [False, True])
def test_retry_after_accepts_naive_and_aware_http_dates(aware):
    retry_at = datetime.now(timezone.utc)
    if not aware:
        retry_at = retry_at.replace(tzinfo=None)
    response = httpx.Response(429, headers={"Retry-After": "date"})

    with patch("app.utils.http.parsedate_to_datetime", return_value=retry_at):
        assert _retry_after_seconds(response) == pytest.approx(0.0, abs=0.1)


@pytest.mark.asyncio
async def test_fetch_with_retry_raises_exhausted_status_error():
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(429, request=request)
    client = MagicMock(get=AsyncMock(return_value=response))

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_with_retry(client, str(request.url), max_retries=0)


@pytest.mark.asyncio
async def test_fetch_with_retry_retries_transport_error_without_logger():
    request = httpx.Request("GET", "https://example.com")
    success = httpx.Response(200, request=request)
    client = MagicMock(
        get=AsyncMock(side_effect=[httpx.ConnectError("offline", request=request), success])
    )

    with patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as sleep:
        assert await fetch_with_retry(client, str(request.url), max_retries=1) is success

    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_with_retry_rejects_negative_retry_count():
    with pytest.raises(AssertionError):
        await fetch_with_retry(MagicMock(), "https://example.com", max_retries=-1)


@pytest.mark.asyncio
async def test_fetch_with_retry_reraises_saved_error_after_shortened_iterator(monkeypatch):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(429, request=request)
    client = MagicMock(get=AsyncMock(return_value=response))
    monkeypatch.setattr(http_utils, "range", lambda _stop: [0], raising=False)

    with (
        patch("app.utils.http.asyncio.sleep", new=AsyncMock()),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await fetch_with_retry(client, str(request.url), max_retries=1)


def test_decode_image_bytes_rejects_unexpected_format():
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")

    with pytest.raises(ValueError, match="Expected JPEG image, got PNG"):
        decode_image_bytes(buffer.getvalue(), expected_format="JPEG")


@pytest.mark.parametrize(
    "race_data",
    [
        {"schedule": []},
        {"schedule": [{"name": "Practice", "datetime": None}]},
        {"schedule": [{"name": "Race"}]},
        {"schedule": [{"name": "Race", "datetime": "invalid"}]},
    ],
)
def test_race_time_conversion_handles_incomplete_schedules(race_data):
    result = convert_race_times_to_timezone(race_data, "UTC")

    assert result["timezone"] == "UTC"


def test_race_time_conversion_returns_original_for_unknown_timezone():
    race_data = {"schedule": [{"name": "Race", "datetime": "2026-01-01T00:00:00+00:00"}]}

    assert convert_race_times_to_timezone(race_data, "Invalid/Timezone") is race_data


def test_rate_limit_uses_unknown_identifier_without_client():
    assert rate_limit._get_client_identifier(_request(client=None)) == "unknown"


def test_rate_limit_can_be_disabled(monkeypatch):
    monkeypatch.setattr(rate_limit.config, "RATE_LIMIT_ENABLED", False)

    rate_limit.enforce_rate_limit(_request(), bucket="api", limit=1)

    assert not rate_limit._RATE_LIMIT_BUCKETS


def test_rate_limit_resets_expired_window(monkeypatch):
    rate_limit._reset_rate_limit_state_for_tests()
    monkeypatch.setattr(rate_limit.config, "RATE_LIMIT_ENABLED", True)
    rate_limit._RATE_LIMIT_BUCKETS["api:127.0.0.1"] = (0.0, 9)
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 61.0)

    rate_limit.enforce_rate_limit(_request(), bucket="api", limit=1)

    assert rate_limit._RATE_LIMIT_BUCKETS["api:127.0.0.1"] == (61.0, 1)


@pytest.mark.parametrize("entry", ["not-a-mapping", {"position": None}])
def test_parse_result_position_rejects_unusable_entries(entry):
    assert parse_result_position(entry) is None


def test_sort_entries_by_position_rejects_non_list():
    assert sort_entries_by_position({"position": 1}) == []


@pytest.mark.parametrize(
    ("translations", "expected_filter"),
    [({"configure": "invalid"}, "ALL"), ({"configure": {"filterLabels": []}}, "ALL")],
)
def test_configure_ui_text_ignores_invalid_nested_mappings(translations, expected_filter):
    assert _build_configure_ui_text(translations)["filterLabels"]["ALL"] == expected_filter


def test_calc_percent_handles_zero_total():
    assert calc_percent(10, 0) == 0


def test_detect_ui_language_rejects_unknown_cookie():
    assert detect_ui_language(_request(cookies="preferredLang=xx")) == "en"


def test_detect_ui_language_accepts_supported_cookie():
    assert detect_ui_language(_request(cookies="preferredLang=cs")) == "cs"
