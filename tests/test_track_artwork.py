"""Tests for the provenance-gated local track-artwork import workflow."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from PIL import Image, ImageDraw

from app.services import track_artwork as artwork
from scripts import manage

PROFILE_COLORS = {
    "modern": ((229, 16, 115), (255, 209, 0), (64, 152, 217)),
    "legacy": ((255, 0, 255), (255, 211, 0), (0, 178, 227)),
}


def _make_source(path: Path, profile: str = "modern", size: tuple[int, int] = (120, 80)) -> str:
    """Create a transparent synthetic F1-style track source and return its digest."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((5, height // 4, width - 6, height * 3 // 4), fill=(21, 21, 30, 255))
    sector_top = height * 7 // 16
    sector_bottom = height * 9 // 16
    third = width // 3
    for index, color in enumerate(PROFILE_COLORS[profile]):
        start = max(6, index * third)
        end = min(width - 7, (index + 1) * third - 1)
        draw.rectangle((start, sector_top, end, sector_bottom), fill=(*color, 255))
    image.save(path, format="PNG")
    return sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    path: Path,
    source_sha256: str,
    *,
    profile: str = "modern",
    dimensions: tuple[int, int] = (120, 80),
) -> None:
    """Write one valid manifest entry for importer tests."""
    payload = {
        "schema_version": 1,
        "tracks": {
            "test_track": {
                "season": 2026,
                "race_slug": "test-race",
                "source_page": "https://www.formula1.com/en/racing/2026/test-race",
                "source_url": "https://media.formula1.com/image/upload/test-track.png",
                "source_sha256": source_sha256,
                "source_dimensions": list(dimensions),
                "source_profile": profile,
                "sector_boundaries": [
                    {"at": [1 / 3, 0.5], "normal_degrees": 90},
                    {"at": [2 / 3, 0.5], "normal_degrees": 90},
                ],
                "rights_review_required": True,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("profile", ["modern", "legacy"])
def test_import_track_builds_all_variants_for_both_source_profiles(tmp_path, profile):
    """Both official color generations should map to the same display semantics."""
    source = tmp_path / "download.png"
    manifest = tmp_path / "sources.json"
    output = tmp_path / "artwork"
    digest = _make_source(source, profile)
    _write_manifest(manifest, digest, profile=profile)

    result = artwork.import_track_artwork(
        source,
        "test_track",
        manifest_path=manifest,
        output_dir=output,
        expected_sha256=digest.upper(),
    )

    assert result.source_sha256 == digest
    assert result.output_dimensions == (120, 80)
    assert all(count >= 16 for count in result.sector_pixels)
    assert [path.name for path in result.output_paths] == [
        "test_track.png",
        "test_track_bw.png",
        "test_track_bwr.png",
        "test_track_bwry.png",
        "test_track_spectra6.png",
    ]
    images = {path.stem: Image.open(path).convert("RGB") for path in result.output_paths}
    samples = ((20, 40), (60, 40), (100, 40))
    assert (
        tuple(images["test_track"].getpixel(point) for point in samples) == PROFILE_COLORS[profile]
    )
    assert (
        tuple(images["test_track_bw"].getpixel(point) for point in samples)
        == ((255, 255, 255),) * 3
    )
    assert (
        tuple(images["test_track_bwr"].getpixel(point) for point in samples)
        == ((255, 255, 255),) * 3
    )
    assert tuple(images["test_track_bwry"].getpixel(point) for point in samples) == (
        (255, 0, 0),
        (255, 255, 255),
        (255, 216, 0),
    )
    assert tuple(images["test_track_spectra6"].getpixel(point) for point in samples) == (
        (255, 0, 0),
        (0, 168, 255),
        (255, 216, 0),
    )
    assert images["test_track"].getpixel((0, 0)) == (255, 255, 255)
    first_boundary_x = round((1 / 3) * (images["test_track"].width - 1))
    assert images["test_track_bw"].getpixel((first_boundary_x, 32)) == (255, 255, 255)
    assert images["test_track_bwr"].getpixel((first_boundary_x, 32)) == (255, 0, 0)


def test_import_downscales_to_source_height_limit(tmp_path):
    """Imported editable PNGs should preserve aspect ratio and never exceed 704 px high."""
    source = tmp_path / "large.png"
    manifest = tmp_path / "sources.json"
    digest = _make_source(source, size=(1000, 800))
    _write_manifest(manifest, digest, dimensions=(1000, 800))

    result = artwork.import_track_artwork(
        source,
        "test_track",
        manifest_path=manifest,
        output_dir=tmp_path / "output",
    )

    assert result.output_dimensions == (880, artwork.TRACK_SOURCE_MAX_HEIGHT)
    assert all(Image.open(path).size == (880, 704) for path in result.output_paths)


def test_hash_mismatch_is_rejected_before_any_output_write(tmp_path, monkeypatch):
    """An unapproved download must not create or replace any editable source file."""
    source = tmp_path / "download.png"
    manifest = tmp_path / "sources.json"
    digest = _make_source(source)
    _write_manifest(manifest, "0" * 64)
    output = tmp_path / "artwork"
    output.mkdir()
    sentinel = output / "test_track.png"
    sentinel.write_bytes(b"keep me")
    writer = Mock()
    monkeypatch.setattr(artwork, "atomic_save_image", writer)

    with pytest.raises(artwork.TrackArtworkError, match="SHA-256 mismatch"):
        artwork.import_track_artwork(
            source,
            "test_track",
            manifest_path=manifest,
            output_dir=output,
        )

    assert digest != "0" * 64
    assert sentinel.read_bytes() == b"keep me"
    assert list(output.iterdir()) == [sentinel]
    writer.assert_not_called()


def test_explicit_hash_must_agree_with_manifest_before_reading_source(tmp_path):
    """A conflicting operator-provided digest should fail before opening the local source."""
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, "1" * 64)

    with pytest.raises(artwork.TrackArtworkError, match="selected manifest entry"):
        artwork.import_track_artwork(
            tmp_path / "does-not-exist.png",
            "test_track",
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_sha256="2" * 64,
        )

    assert not (tmp_path / "output").exists()


def test_import_rejects_non_png_dimensions_and_missing_sector_colors(tmp_path):
    """Format, dimensions, and profile recognition must all be validated before writes."""
    source = tmp_path / "source.png"
    manifest = tmp_path / "sources.json"
    output = tmp_path / "output"
    Image.new("RGB", (120, 80), "black").save(source, format="JPEG")
    _write_manifest(manifest, sha256(source.read_bytes()).hexdigest())
    with pytest.raises(artwork.TrackArtworkError, match="must be a PNG"):
        artwork.import_track_artwork(
            source, "test_track", manifest_path=manifest, output_dir=output
        )

    digest = _make_source(source)
    _write_manifest(manifest, digest, dimensions=(121, 80))
    with pytest.raises(artwork.TrackArtworkError, match="dimensions mismatch"):
        artwork.import_track_artwork(
            source, "test_track", manifest_path=manifest, output_dir=output
        )

    Image.new("RGB", (120, 80), "white").save(source, format="PNG")
    _write_manifest(manifest, sha256(source.read_bytes()).hexdigest())
    with pytest.raises(artwork.TrackArtworkError, match="sector 1"):
        artwork.import_track_artwork(
            source, "test_track", manifest_path=manifest, output_dir=output
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(tracks={}), "at least one track"),
        (
            lambda data: data["tracks"].__setitem__("Bad ID!", data["tracks"].pop("test_track")),
            "Invalid circuit id",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(season=1949),
            "season",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(race_slug="Bad slug"),
            "race_slug",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(
                source_page="https://example.com/not-official"
            ),
            "official Formula 1 host",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(source_url=None),
            "must be a URL",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(source_sha256="short"),
            "64 hexadecimal",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(source_dimensions=[120]),
            "source_dimensions",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(source_dimensions=[True, 80]),
            "positive integers",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(source_profile="future"),
            "source_profile",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(sector_boundaries=[]),
            "sector_boundaries",
        ),
        (
            lambda data: data["tracks"]["test_track"]["sector_boundaries"][0].update(at=None),
            r"must be \[normalized_x",
        ),
        (
            lambda data: data["tracks"]["test_track"]["sector_boundaries"][0].update(at=[2, 0.5]),
            "finite values from 0 to 1",
        ),
        (
            lambda data: data["tracks"]["test_track"]["sector_boundaries"][0].update(
                normal_degrees=float("nan")
            ),
            "normal_degrees must be finite",
        ),
        (
            lambda data: data["tracks"]["test_track"].update(rights_review_required="yes"),
            "rights_review_required",
        ),
        (
            lambda data: data["tracks"].update(test_track=None),
            "tracks.test_track must be a JSON object",
        ),
    ],
)
def test_manifest_loader_rejects_unsafe_or_unknown_metadata(tmp_path, mutation, message):
    """The versioned manifest should fail closed when provenance metadata drifts."""
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, "0" * 64)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    mutation(data)
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(artwork.TrackArtworkError, match=message):
        artwork.load_track_source_manifest(manifest)


def test_manifest_loader_reports_missing_invalid_and_unknown_entries(tmp_path):
    """Missing files, malformed JSON, and absent circuit IDs should have clear errors."""
    manifest = tmp_path / "sources.json"
    with pytest.raises(artwork.TrackArtworkError, match="not found"):
        artwork.load_track_source_manifest(manifest)
    manifest.write_text("{", encoding="utf-8")
    with pytest.raises(artwork.TrackArtworkError, match="Cannot read"):
        artwork.load_track_source_manifest(manifest)
    _write_manifest(manifest, "0" * 64)
    with pytest.raises(artwork.TrackArtworkError, match="not present"):
        artwork.import_track_artwork(
            tmp_path / "missing.png",
            "unknown",
            manifest_path=manifest,
            output_dir=tmp_path / "output",
        )
    with pytest.raises(artwork.TrackArtworkError, match="Cannot read track source"):
        artwork.import_track_artwork(
            tmp_path / "missing.png",
            "test_track",
            manifest_path=manifest,
            output_dir=tmp_path / "output",
        )


def test_manifest_and_png_decoders_reject_non_objects_and_corruption(tmp_path):
    """Top-level JSON types and corrupt PNG payloads should produce domain errors."""
    manifest = tmp_path / "sources.json"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(artwork.TrackArtworkError, match="manifest must be a JSON object"):
        artwork.load_track_source_manifest(manifest)

    manifest.write_text(json.dumps({"schema_version": 1, "tracks": []}), encoding="utf-8")
    with pytest.raises(artwork.TrackArtworkError, match="manifest.tracks must be a JSON object"):
        artwork.load_track_source_manifest(manifest)

    source = tmp_path / "corrupt.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\ncorrupt")
    _write_manifest(manifest, sha256(source.read_bytes()).hexdigest())
    with pytest.raises(artwork.TrackArtworkError, match="Invalid PNG source"):
        artwork.import_track_artwork(
            source,
            "test_track",
            manifest_path=manifest,
            output_dir=tmp_path / "output",
        )


def test_manage_import_routes_local_source_and_optionally_preprocesses(monkeypatch, tmp_path):
    """The CLI should import once and regenerate all four existing runtime palettes on request."""
    source = tmp_path / "download.png"
    manifest = tmp_path / "sources.json"
    result = artwork.TrackImportResult(
        circuit_id="test_track",
        source_sha256="a" * 64,
        source_dimensions=(3840, 2160),
        output_dimensions=(1252, 704),
        output_paths=(),
        sector_pixels=(10, 20, 30),
    )
    importer = Mock(return_value=result)
    preprocessor = Mock()
    monkeypatch.setattr(manage, "import_track_artwork", importer)
    monkeypatch.setattr(manage, "preprocess_tracks", preprocessor)

    assert (
        manage.main(
            [
                "import",
                "track",
                "--source",
                str(source),
                "--circuit",
                "test_track",
                "--manifest",
                str(manifest),
                "--expected-sha256",
                "A" * 64,
                "--preprocess",
            ]
        )
        == 0
    )

    importer.assert_called_once_with(
        source,
        "test_track",
        manifest_path=manifest,
        expected_sha256="A" * 64,
    )
    assert preprocessor.call_args_list == [
        call(palette, ["test_track"]) for palette in manage.PREPROCESS_PALETTES
    ]
