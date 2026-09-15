"""Regression coverage for semantic track import and device-size readability."""

import json
from hashlib import sha256

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from app.services import asset_preprocessing as assets
from app.services import track_artwork as artwork
from app.services import track_assets
from app.services.spectra6_renderer import SPECTRA6_THEME, Spectra6Colors
from app.utils.bmp import quantize_to_palette
from tests.test_asset_preprocessing import _write_managed_bundle
from tests.test_track_artwork import PROFILE_COLORS, _make_source, _write_manifest


def test_webp_import_preserves_original_digest_and_detached_blue_text(tmp_path):
    """Lossless WebP needs no PNG conversion, and blue lettering is not a sector stroke."""
    source = tmp_path / "original.webp"
    _make_source(source)
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    ImageDraw.Draw(image).rectangle((4, 3, 18, 8), fill=(*PROFILE_COLORS["modern"][2], 255))
    image.save(source, format="WEBP", lossless=True)
    digest = sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, digest)
    result = artwork.import_track_artwork(
        source, "test_track", manifest_path=manifest, output_dir=tmp_path / "out"
    )
    assert result.source_sha256 == digest
    for path in result.output_paths:
        with Image.open(path) as variant:
            assert variant.getpixel((10, 5)) == PROFILE_COLORS["modern"][2]
            assert variant.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_import_rejects_animation_before_publication(tmp_path, image_format):
    """A multi-frame source must not silently import only its first frame."""
    source = tmp_path / "animated"
    Image.new("RGB", (120, 80), "red").save(
        source,
        format=image_format,
        save_all=True,
        append_images=[Image.new("RGB", (120, 80), "blue")],
        duration=100,
    )
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, sha256(source.read_bytes()).hexdigest())
    with pytest.raises(artwork.TrackArtworkError, match="static image"):
        artwork.import_track_artwork(
            source, "test_track", manifest_path=manifest, output_dir=tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("boundary", [{"at": [0.05, 0.05]}, {"at": [2 / 3, 0.5]}])
def test_import_rejects_boundary_away_from_expected_join(tmp_path, boundary):
    """A misplaced or swapped S1/S2 separator cannot overwrite a reviewed bundle."""
    source = tmp_path / "source.png"
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, _make_source(source))
    payload = json.loads(manifest.read_text())
    payload["tracks"]["test_track"]["sector_boundaries"][0] = boundary
    manifest.write_text(json.dumps(payload))
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "test_track.bundle.json"
    sentinel.write_bytes(b"reviewed")
    with pytest.raises(artwork.TrackArtworkError, match="S1/S2 join"):
        artwork.import_track_artwork(
            source, "test_track", manifest_path=manifest, output_dir=output
        )
    assert sentinel.read_bytes() == b"reviewed"
    assert list(output.iterdir()) == [sentinel]


@pytest.mark.parametrize("transpose", [False, True])
def test_separator_normals_follow_stroke_and_stay_inside_road(transpose):
    """Normals follow horizontal/vertical joins and never paint beside the road."""
    masks = [Image.new("L", (120, 80)) for _ in range(3)]
    for index, mask in enumerate(masks):
        ImageDraw.Draw(mask).rectangle((10 + index * 30, 38, 39 + index * 30, 42), fill=255)
    road = Image.new("L", (120, 80))
    ImageDraw.Draw(road).rectangle((10, 34, 100, 46), fill=255)
    points = ((39 / 119, 40 / 79), (69 / 119, 40 / 79))
    if transpose:
        masks = [mask.transpose(Image.Transpose.TRANSPOSE) for mask in masks]
        road = road.transpose(Image.Transpose.TRANSPOSE)
        points = tuple((y, x) for x, y in points)
    boundaries = artwork._resolve_sector_boundaries(
        tuple(masks), tuple(artwork.SectorBoundary(p) for p in points)
    )
    assert all(b.normal_degrees == pytest.approx(0 if transpose else 90) for b in boundaries)
    result = Image.new("RGB", road.size, "white")
    result.paste((0, 0, 0), mask=road)
    artwork._draw_sector_separators(result, boundaries, (255, 0, 0), road)
    for pixel, on_road in zip(result.get_flattened_data(), road.get_flattened_data(), strict=True):
        if not on_road:
            assert pixel == (255, 255, 255)
    assert (255, 0, 0) in result.get_flattened_data()


@pytest.mark.parametrize(
    "callouts",
    [
        None,
        [None],
        [{}],
        [{"box": [0, 0, 0, 1]}],
        [{"box": [False, 0, 1, 1]}],
        [{"box": [0, 0, float("nan"), 1]}],
        [{"box": [0, 0, 1, 1], "text": ["bad\nline"]}],
        [{"box": [0, 0, 1, 1], "text": []}],
        [{"box": [0, 0, 1, 1], "text": ["č"]}],
    ],
)
def test_manifest_rejects_invalid_callout_overlays(tmp_path, callouts):
    """Malformed overlay metadata fails before an importer can draw or publish."""
    manifest = tmp_path / "sources.json"
    _write_manifest(manifest, "a" * 64)
    payload = json.loads(manifest.read_text())
    payload["tracks"]["test_track"]["callouts"] = callouts
    manifest.write_text(json.dumps(payload))
    with pytest.raises(artwork.TrackArtworkError, match="callouts"):
        artwork.load_track_source_manifest(manifest)


def test_too_small_callout_box_is_rejected():
    """An overlay that cannot fit readable text must not be silently clipped."""
    source = Image.new("RGB", (100, 100), "white")
    with pytest.raises(artwork.TrackArtworkError, match="too small"):
        artwork._draw_callouts(
            source.copy(),
            source,
            (artwork.TrackCallout((0, 0, 0.01, 0.01), ("SPEED TRAP",)),),
            "bw",
        )


def test_legacy_default_manifest_cannot_republish_artwork(tmp_path):
    with pytest.raises(artwork.TrackArtworkError, match="retired"):
        artwork.import_track_artwork(tmp_path / "input.png", "madring", output_dir=tmp_path)


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_mono_track_flattens_transparent_dark_pixels_to_white(tmp_path, mode):
    """Invisible black pixels must not become a black background or expand the crop."""
    source = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((20, 10, 39, 29), fill=(0, 0, 0, 255))
    path = tmp_path / "transparent.png"
    source.convert(mode).save(path)
    output = tmp_path / "mono.bmp"
    assert assets.process_track_image(path, output, "mono")["final_dimensions"] == (20, 20)


def test_semantic_spectra6_keeps_neutral_details_achromatic(tmp_path):
    """Thin gray lettering needs black ink while an actual blue sector stays blue."""
    image = Image.new("RGB", (30, 10), (150, 150, 150))
    ImageDraw.Draw(image).rectangle((10, 0, 19, 9), fill=(0, 168, 255))
    ImageDraw.Draw(image).rectangle((20, 0, 29, 9), fill=(230, 230, 230))
    metadata = PngInfo()
    metadata.add_text(
        track_assets.TRACK_PROCESSING_PROFILE_KEY, track_assets.TRACK_PROCESSING_PROFILE
    )
    source, output = tmp_path / "semantic.png", tmp_path / "spectra6.bmp"
    image.save(source, pnginfo=metadata)
    assets.process_track_image(source, output, "spectra6")
    with Image.open(output) as opened:
        result = opened.convert("RGB")
    assert result.getpixel((5, 5)) == (0, 0, 0)
    assert result.getpixel((15, 5)) == (0, 168, 255)
    assert result.getpixel((25, 5)) == (255, 255, 255)


def test_spectra6_track_resize_does_not_reintroduce_blue_edges():
    """Final calendar resizing of a neutral track must stay monochrome after quantization."""
    source = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(source).line((10, 60, 108, 8), fill="black", width=7)
    prepared = SPECTRA6_THEME.prepare_track_image(source, 49, 28, None)
    indexed = quantize_to_palette(prepared, Spectra6Colors.PALETTE, 6)
    colors = set(indexed.convert("RGB").get_flattened_data())
    assert colors == {(0, 0, 0), (255, 255, 255)}


def test_crop_preserves_threshold_edges_and_extreme_aspect_ratios():
    """A pixel below 245 remains in the crop and a thin fit cannot round to zero."""
    image = Image.new("RGB", (10, 10), (245, 245, 245))
    image.putpixel((3, 4), (255, 255, 244))
    image.putpixel((7, 6), (244, 255, 255))
    assert assets._crop_non_white(image).size == (5, 3)
    assert assets._fit_track(Image.new("RGB", (1, 1000))).size == (1, 280)
    assert assets._fit_track(Image.new("RGB", (1000, 1))).size == (490, 1)


def test_custom_manifest_is_validated_during_preprocessing(tmp_path):
    """Import --manifest must not silently validate against a different manifest later."""
    source = _write_managed_bundle(tmp_path)
    custom = tmp_path / "custom.json"
    (source / "sources.json").replace(custom)
    (source / "managed_bwr.png").write_bytes(b"changed")
    with pytest.raises(assets.PreprocessingError, match="hash mismatch"):
        assets.preprocess_tracks(
            "bwr", source_dir=source, output_dir=tmp_path / "out", manifest_path=custom
        )
    assert not (tmp_path / "out").exists()


def test_legacy_default_output_cannot_republish_artwork(tmp_path):
    with pytest.raises(artwork.TrackArtworkError, match="retired"):
        artwork.import_track_artwork(
            tmp_path / "input.png", "monza", manifest_path=tmp_path / "sources.json"
        )


def test_synthetic_callouts_render_all_palettes_without_archived_art(tmp_path):
    """Own rectangular source exercises readable overlays without redistributing an F1 map."""
    source, manifest = tmp_path / "own.png", tmp_path / "sources.json"
    digest = _make_source(source, size=(600, 400))
    _write_manifest(manifest, digest, dimensions=(600, 400))
    payload = json.loads(manifest.read_text())
    payload["tracks"]["test_track"]["callouts"] = [
        {"box": [0.1, 0.02, 0.5, 0.2], "text": ["OWN LABEL"]}
    ]
    manifest.write_text(json.dumps(payload))
    result = artwork.import_track_artwork(
        source, "test_track", manifest_path=manifest, output_dir=tmp_path / "out"
    )
    for path in result.output_paths:
        if path.name == "test_track.png":
            continue
        with Image.open(path) as image:
            panel = image.crop((60, 8, 300, 80))
            colors = set(panel.get_flattened_data())
            foreground = (0, 0, 0) if "spectra6" in path.name else (255, 255, 255)
            assert foreground in colors
            assert len(colors) >= 2
