"""Focused tests for shared renderer helpers."""

from cachetools import LRUCache
from PIL import Image

from app.models import TeamDriverEntry
from app.services import renderer_common


def test_select_active_team_drivers_uses_latest_roster_range():
    drivers = [
        TeamDriverEntry(name="Jack Doohan", rounds="1–6"),
        TeamDriverEntry(name="Franco Colapinto", rounds="7–24"),
        TeamDriverEntry(name="Pierre Gasly", rounds="All"),
    ]

    selected = renderer_common.select_active_team_drivers(drivers)

    assert [driver.name for driver in selected] == ["Franco Colapinto", "Pierre Gasly"]


def test_decoded_image_cache_evicts_by_bytes(tmp_path, monkeypatch):
    cache = LRUCache(maxsize=100, getsizeof=renderer_common._decoded_image_size)
    monkeypatch.setattr(renderer_common, "_DECODED_IMAGE_CACHE", cache)

    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    Image.new("RGB", (5, 5), "white").save(first_path)
    Image.new("RGB", (5, 5), "black").save(second_path)

    renderer_common._load_image_file(str(first_path), first_path.stat().st_mtime_ns)
    renderer_common._load_image_file(str(second_path), second_path.stat().st_mtime_ns)

    assert cache.currsize <= cache.maxsize
    assert len(cache) == 1
