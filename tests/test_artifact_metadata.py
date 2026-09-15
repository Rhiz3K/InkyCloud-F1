"""Regression coverage for generation identity, age, and concurrent file replacement."""

import json
import time
from unittest.mock import patch

import pytest

from app.routes import images, previews
from app.services import artifact_metadata as metadata
from app.services import scheduler_generation
from app.services.generation_freshness import PREGENERATED_MAX_AGE_SECONDS
from app.services.track_catalog import DEFAULT_TRACK_OPTIONS
from app.utils.etag import strong_etag


@pytest.mark.parametrize(
    ("race", "expected"),
    [
        (None, ""),
        ({"race_key": "2026-monza"}, "calendar:v3:2026-monza"),
        (
            {"season": 2026, "round": 16, "circuit": {"circuitId": "monza"}, "date": "2026-09-06"},
            "calendar:v3:2026:16:monza:2026-09-06",
        ),
    ],
)
def test_calendar_identity(race, expected):
    assert metadata.calendar_identity(race) == (
        f"{expected}:{DEFAULT_TRACK_OPTIONS.cache_key}" if expected else ""
    )


@pytest.mark.asyncio
async def test_previous_footer_layout_is_not_reused_for_bmp_or_preview(tmp_path):
    """A fresh cached image with the old footer still requires regeneration."""
    source = tmp_path / "calendar_en.bmp"
    preview = source.with_suffix(".png")
    old_identity = f"calendar:v2:2026-madring:{DEFAULT_TRACK_OPTIONS.cache_key}"
    current_identity = metadata.calendar_identity({"race_key": "2026-madring"})
    await scheduler_generation._write_bmp_artifact(source, b"bmp", old_identity)
    source_data = metadata.read_artifact_metadata(source)
    preview.write_bytes(b"png")
    await metadata.write_artifact_metadata(
        preview, b"png", old_identity, source=(source, source_data)
    )

    assert metadata.read_artifact_metadata(preview) is not None
    assert not metadata.artifact_matches(source, current_identity)
    assert not metadata.artifact_matches(preview, current_identity)


@pytest.mark.asyncio
async def test_preview_inherits_source_age_and_rejects_replaced_source(tmp_path):
    source = tmp_path / "calendar_en.bmp"
    await scheduler_generation._write_bmp_artifact(source, b"bmp", "calendar:monza")
    source_data = metadata.read_artifact_metadata(source)
    assert source_data is not None
    preview = tmp_path / "configure_calendar_en.png"
    await scheduler_generation._write_preview_artifact(preview, b"png", source, source_data)
    assert metadata.read_artifact_metadata(preview)["generated_at"] == source_data["generated_at"]
    assert previews._preview_snapshot(preview, "calendar:monza")[0] == b"png"
    assert previews._preview_snapshot(preview, "calendar:madring") is None
    with patch.object(
        metadata.time, "time", return_value=time.time() + PREGENERATED_MAX_AGE_SECONDS + 1
    ):
        assert metadata.read_artifact_metadata(source) is None
        assert metadata.read_artifact_metadata(preview) is None
    await scheduler_generation._write_bmp_artifact(source, b"new bmp", "calendar:madring")
    assert metadata.read_artifact_metadata(preview) is None
    # Conversion started before the source changed: never publish its obsolete result.
    await scheduler_generation._write_preview_artifact(preview, b"obsolete", source, source_data)
    assert preview.read_bytes() == b"png"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        [],
        {"schema": 2},
        {"mtime_ns": 0},
        {"size": -1},
        {"generated_at": "invalid"},
        {"generated_at": float("nan")},
        {"generated_at": float("inf")},
        {"identity": 4},
        {"etag": None},
        {"source": "../outside.bmp"},
        {"source": 4},
        {"source": "other.png"},
        {"source": "missing.bmp"},
        {"source": "self.bmp"},
        {"source": "source.bmp", "source_etag": "wrong"},
        {"source": "source.bmp", "source_etag": strong_etag(b"bmp"), "identity": "other"},
    ],
)
async def test_reject_invalid_or_unbound_metadata(tmp_path, change):
    path = tmp_path / "test.png"
    path.write_bytes(b"png")
    await metadata.write_artifact_metadata(path, b"png", "calendar:test")
    await scheduler_generation._write_bmp_artifact(tmp_path / "source.bmp", b"bmp", "calendar:test")
    sidecar = metadata.metadata_path(path)
    data = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps(data | change if isinstance(change, dict) else change))
    assert metadata.read_artifact_metadata(path) is None
    assert not metadata.artifact_matches(path, "calendar:test")


@pytest.mark.asyncio
async def test_missing_corrupt_and_tampered_preview(tmp_path):
    path = tmp_path / "test.png"
    assert metadata.read_artifact_metadata(path) is None
    metadata.metadata_path(path).write_text("{broken")
    assert metadata.read_artifact_metadata(path) is None
    path.write_bytes(b"png")
    await metadata.write_artifact_metadata(path, b"png", "calendar:test")
    data = metadata.read_artifact_metadata(path)
    with patch.object(previews, "read_artifact_metadata", return_value=data):
        path.write_bytes(b"bad")  # Same byte count; hash still protects the snapshot.
        assert previews._preview_snapshot(path, "calendar:test") is None
        path.unlink()
        assert previews._preview_snapshot(path, "calendar:test") is None
    path.write_bytes(b"png")
    with patch.object(previews, "read_artifact_metadata", side_effect=[data, None]):
        assert previews._preview_snapshot(path, "calendar:test") is None


@pytest.mark.asyncio
async def test_bmp_cannot_reference_another_source(tmp_path):
    source = tmp_path / "source.bmp"
    source.write_bytes(b"bmp")
    await metadata.write_artifact_metadata(source, b"bmp", "calendar:test")
    data = metadata.read_artifact_metadata(source)
    await metadata.write_artifact_metadata(source, b"bmp", "calendar:test", source=(source, data))
    assert metadata.read_artifact_metadata(source) is None


@pytest.mark.asyncio
async def test_bmp_reader_revalidates_identity_before_body_and_304(tmp_path):
    source = tmp_path / "calendar_en.bmp"
    await scheduler_generation._write_bmp_artifact(source, b"bmp", "calendar:test")
    etag = strong_etag(b"bmp")
    assert await images._read_pregenerated_artifact(source, None, "calendar:wrong") == (None, None)
    assert await images._read_pregenerated_artifact(source, etag, "calendar:test") == (None, etag)
    for header in (None, etag):
        with patch.object(images, "artifact_matches", side_effect=[True, False]):
            assert await images._read_pregenerated_artifact(source, header, "calendar:test") == (
                None,
                None,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", ["calendar", "teams"])
async def test_expired_bmp_is_not_rejuvenated_by_preview_conversion(tmp_path, monkeypatch, screen):
    source = tmp_path / ("calendar_en.bmp" if screen == "calendar" else "teams_2026_en.bmp")
    await scheduler_generation._write_bmp_artifact(source, b"bmp", "calendar:old")
    monkeypatch.setattr(scheduler_generation.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(scheduler_generation, "SUPPORTED_LANGUAGES", ["en"])
    with patch.object(metadata.time, "time", return_value=time.time() + 7 * 86400):
        await scheduler_generation.generate_preview_pngs(["off"], 2026)
    assert not (tmp_path / f"configure_{screen}_en.png").exists()
