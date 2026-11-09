import pytest

from app.state import (
    BMP_CACHE_MAXSIZE,
    BMP_CACHE_TTL_SECONDS,
    clear_bmp_cache,
    get_and_clear_api_calls_buffer,
    get_bmp_cache,
    get_bmp_variant_cache,
    record_api_call,
)
from app.utils.etag import strong_etag


@pytest.fixture(autouse=True)
def reset_state():
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    get_and_clear_api_calls_buffer()
    yield
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    get_and_clear_api_calls_buffer()


class TestBmpCache:
    @staticmethod
    def test_get_bmp_cache_returns_cache():
        cache = get_bmp_cache()
        assert cache is not None
        assert hasattr(cache, "maxsize")
        assert cache.maxsize == BMP_CACHE_MAXSIZE

    @staticmethod
    def test_clear_bmp_cache():
        cache = get_bmp_cache()
        variants = get_bmp_variant_cache()
        cache["test_key"] = (b"test_data", strong_etag(b"test_data"))
        variants["extra"] = (b"extra", strong_etag(b"extra"))
        assert len(cache) == 1
        assert len(variants) == 1

        clear_bmp_cache()
        assert len(cache) == 0
        assert len(variants) == 0

    @staticmethod
    def test_bmp_cache_set_and_get():
        cache = get_bmp_cache()
        artifact = (b"bmp_data", strong_etag(b"bmp_data"))
        cache["calendar_en"] = artifact
        assert cache["calendar_en"] == artifact

    @staticmethod
    def test_bmp_cache_capacity_covers_localized_variants():
        # 13 languages x 4 display modes x 3 weather modes = 156 base variants.
        assert BMP_CACHE_MAXSIZE >= 156

    @staticmethod
    def test_variant_cache_is_smaller_and_uses_the_main_ttl(monkeypatch):
        cache = get_bmp_variant_cache()

        assert cache is get_bmp_variant_cache()
        assert cache.maxsize == 64
        assert cache.ttl == BMP_CACHE_TTL_SECONDS
        assert cache.maxsize < BMP_CACHE_MAXSIZE

        monkeypatch.setattr("app.state.config.BMP_VARIANT_CACHE_MAXSIZE", 128)
        grown = get_bmp_variant_cache()
        assert grown is not cache
        assert grown.maxsize == 128
        assert len(grown) == 0

    @staticmethod
    def test_variant_cache_eviction_does_not_touch_main_entries(monkeypatch):
        main = get_bmp_cache()
        main["calendar"] = (b"calendar", strong_etag(b"calendar"))
        variant = get_bmp_variant_cache()
        for name in (b"a", b"b", b"c"):
            variant[name.decode()] = (name, strong_etag(name))

        monkeypatch.setattr("app.state.config.BMP_VARIANT_CACHE_MAXSIZE", 1)
        shrunk = get_bmp_variant_cache()

        assert shrunk is not variant
        assert list(shrunk) == ["c"]
        assert main["calendar"] == (b"calendar", strong_etag(b"calendar"))
        assert len(main) == 1


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
    @pytest.mark.parametrize("display_type", ["bwr", "bwry"])
    def test_record_api_call_full(display_type: str):
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
            display_type=display_type,
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
        assert call["display_type"] == display_type
        assert call["status_code"] == 200

    @staticmethod
    def test_record_api_call_status_code():
        record_api_call("/calendar.bmp", 1.0, 0, status_code=304)

        calls = get_and_clear_api_calls_buffer()
        assert calls[0]["status_code"] == 304

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
