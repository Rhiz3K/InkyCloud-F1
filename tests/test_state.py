import pytest

from app.state import (
    clear_bmp_cache,
    get_and_clear_api_calls_buffer,
    get_bmp_cache,
    record_api_call,
)


@pytest.fixture(autouse=True)
def reset_state():
    get_bmp_cache().clear()
    get_and_clear_api_calls_buffer()
    yield
    get_bmp_cache().clear()
    get_and_clear_api_calls_buffer()


class TestBmpCache:
    @staticmethod
    def test_get_bmp_cache_returns_cache():
        cache = get_bmp_cache()
        assert cache is not None
        assert hasattr(cache, "maxsize")
        assert cache.maxsize == 100

    @staticmethod
    def test_clear_bmp_cache():
        cache = get_bmp_cache()
        cache["test_key"] = b"test_data"
        assert len(cache) == 1

        clear_bmp_cache()
        assert len(cache) == 0

    @staticmethod
    def test_bmp_cache_set_and_get():
        cache = get_bmp_cache()
        cache["calendar_en"] = b"bmp_data"
        assert cache["calendar_en"] == b"bmp_data"


class TestApiCallsBuffer:
    @staticmethod
    def test_record_api_call_minimal():
        record_api_call("/calendar.bmp", 100.0, 1024)

        calls = get_and_clear_api_calls_buffer()
        assert len(calls) == 1
        assert calls[0]["endpoint"] == "/calendar.bmp"
        assert calls[0]["response_time_ms"] == 100.0
        assert calls[0]["response_size_bytes"] == 1024

    @staticmethod
    def test_record_api_call_full():
        record_api_call(
            "/calendar.bmp",
            150.5,
            2048,
            lang="cs",
            tz="Europe/Prague",
            year=2026,
            round_num=5,
            race_name="Monaco Grand Prix",
            is_auto_selected=True,
            display_type="bwr",
        )

        calls = get_and_clear_api_calls_buffer()
        assert len(calls) == 1
        call = calls[0]
        assert call["lang"] == "cs"
        assert call["tz"] == "Europe/Prague"
        assert call["year"] == 2026
        assert call["round"] == 5
        assert call["race_name"] == "Monaco Grand Prix"
        assert call["is_auto_selected"] == 1
        assert call["display_type"] == "bwr"

    @staticmethod
    def test_record_api_call_is_auto_selected_false():
        record_api_call("/teams.bmp", 50.0, 512, is_auto_selected=False)

        calls = get_and_clear_api_calls_buffer()
        assert calls[0]["is_auto_selected"] == 0

    @staticmethod
    def test_get_and_clear_clears_buffer():
        record_api_call("/test1", 10.0, 100)
        record_api_call("/test2", 20.0, 200)

        calls = get_and_clear_api_calls_buffer()
        assert len(calls) == 2

        calls_after = get_and_clear_api_calls_buffer()
        assert len(calls_after) == 0

    @staticmethod
    def test_multiple_calls_accumulate():
        for i in range(5):
            record_api_call(f"/endpoint{i}", float(i * 10), i * 100)

        calls = get_and_clear_api_calls_buffer()
        assert len(calls) == 5

    @staticmethod
    def test_call_has_timestamp():
        record_api_call("/calendar.bmp", 100.0, 1024)

        calls = get_and_clear_api_calls_buffer()
        assert "timestamp" in calls[0]
        assert "T" in calls[0]["timestamp"]
