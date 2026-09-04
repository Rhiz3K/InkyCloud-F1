"""Behavioral and byte-regression tests for the unified asset CLI backend."""

from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services import asset_preprocessing as assets
from app.services import track_assets
from scripts import manage


@pytest.fixture
def source_art(tmp_path) -> Path:
    """Create deterministic source art exercising transparency and every display color."""
    source = tmp_path / "source.png"
    image = Image.new("RGBA", (640, 360), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 40, 590, 320), fill=(255, 255, 255, 255))
    draw.line((80, 280, 560, 70), fill=(0, 0, 0, 255), width=17)
    draw.ellipse((180, 100, 310, 230), fill=(255, 0, 0, 220))
    draw.polygon(((350, 90), (550, 160), (390, 260)), fill=(255, 216, 0, 255))
    draw.rectangle((300, 145, 420, 200), fill=(0, 168, 255, 255))
    image.save(source)
    return source


def _write_managed_bundle(root: Path, *, write_marker: bool = True) -> Path:
    """Create a small manifest-managed source set with an optional valid marker."""
    source_dir = root / "tracks"
    source_dir.mkdir()
    paths = track_assets.track_bundle_paths(source_dir, "managed")
    colors = {
        "generic": "black",
        "bw": "white",
        "bwr": "red",
        "bwry": "yellow",
        "spectra6": "blue",
    }
    for variant, path in paths.items():
        Image.new("RGB", (20, 10), colors[variant]).save(path, format="PNG")

    source_sha = "a" * 64
    manifest = {
        "schema_version": 1,
        "tracks": {
            "managed": {
                "season": 2026,
                "race_slug": "managed-race",
                "source_page": "https://www.formula1.com/en/racing/2026/managed-race",
                "source_url": "https://media.formula1.com/image/upload/managed.png",
                "source_sha256": source_sha,
                "source_dimensions": [20, 10],
                "source_profile": "modern",
                "sector_boundaries": [
                    {"at": [0.3, 0.5], "normal_degrees": 90},
                    {"at": [0.6, 0.5], "normal_degrees": 90},
                ],
                "rights_review_required": True,
            }
        },
    }
    (source_dir / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_marker:
        png_bytes = {variant: path.read_bytes() for variant, path in paths.items()}
        marker = track_assets.encode_track_bundle_marker("managed", source_sha, png_bytes)
        (source_dir / "managed.bundle.json").write_bytes(marker)
    return source_dir


@pytest.mark.parametrize(
    ("palette", "expected_hash"),
    [
        ("mono", "14d218b73f39493f1aa79aa6b2154e7f26126d08ba9e4e7339031bf5bece432b"),
        ("bwr", "0177178e7aebda72306ab346a211e158a7f5782a8918e6498e5eec032591e499"),
        ("bwry", "734e0db99b37e942d7228fde317a133800c72a8ee5dd223fed9076244b3d7e21"),
        (
            "spectra6",
            "a2ef3c537a7c597324f03e45bb5e654756fa75788a3a12d90de33d42d2e7b559",
        ),
    ],
)
def test_track_preprocessing_is_byte_identical_to_legacy_scripts(
    tmp_path, source_art, palette, expected_hash
):
    """Every track palette should retain the pre-consolidation BMP bytes."""
    output = tmp_path / f"track-{palette}.bmp"

    stats = assets.process_track_image(source_art, output, palette)

    assert sha256(output.read_bytes()).hexdigest() == expected_hash
    assert stats["input_size"] == source_art.stat().st_size
    assert stats["output_size"] == output.stat().st_size
    assert stats["original_dimensions"] == (640, 360)
    assert stats["final_dimensions"][0] <= assets.TRACK_MAX_WIDTH
    assert stats["final_dimensions"][1] <= assets.TRACK_MAX_HEIGHT
    assert stats["compression_ratio"] > 0


@pytest.mark.parametrize(
    ("palette", "expected_hash"),
    [
        ("mono", "46986deefdf5bb5ab8bba00ca12860a5025ce1f686c32a33cdd313cbc891569c"),
        ("bwr", "02668f54cbfff1e6c393123f8eac32300ea3946a8d7ac97e98b9ce2e8e870dc5"),
        ("bwry", "ff11091e521fccb4a65460132a95a7b55650371a13aa83661c805cf13256a983"),
        (
            "spectra6",
            "39f1ecc3f9c16ad3ee3ef616bbd6de8fd1376e25aceb28fa809ede8d1d27b964",
        ),
    ],
)
def test_flag_preprocessing_is_byte_identical_to_legacy_scripts(
    tmp_path, source_art, palette, expected_hash
):
    """Every flag palette should retain the pre-consolidation BMP bytes."""
    output = tmp_path / f"flag-{palette}.bmp"

    stats = assets.process_flag_image(source_art, output, palette)

    assert sha256(output.read_bytes()).hexdigest() == expected_hash
    assert stats["original_dimensions"] == (640, 360)
    assert stats["final_dimensions"] == (assets.FLAG_WIDTH, assets.FLAG_HEIGHT)
    if palette == "mono":
        assert stats["num_colors"] == len(stats["color_mappings"])
    else:
        assert "num_colors" not in stats


def test_palette_names_derive_from_renderer_display_types():
    """CLI palettes should expose exactly the renderer-owned display modes."""
    assert assets.PREPROCESS_PALETTES == ("mono", "bwr", "bwry", "spectra6")
    assert [assets.get_palette_spec(name).name for name in assets.PREPROCESS_PALETTES] == list(
        assets.PREPROCESS_PALETTES
    )
    with pytest.raises(ValueError, match="Unknown palette"):
        assets.get_palette_spec("sepia")


@pytest.mark.parametrize(
    ("pattern", "black_pixels"),
    [
        ("solid_white", 0),
        ("unknown", 0),
        ("solid_black", 36),
        ("dense_crosshatch", 27),
        ("vertical_lines", 18),
        ("horizontal_lines", 18),
        ("diagonal_lines", 12),
        ("checkerboard", 20),
        ("sparse_dots", 4),
        ("very_sparse_dots", 1),
    ],
)
def test_pattern_tiles_keep_the_legacy_density(pattern, black_pixels):
    """All monochrome flag patterns should remain deterministic."""
    tile = assets.create_pattern_tile(pattern)
    assert int(np.sum(tile == 0)) == black_pixels


def test_color_analysis_and_pattern_assignment_cover_empty_and_dark_palettes():
    """Color helpers should handle empty, single, and entirely dark palettes."""
    labels = np.array([[0, 0], [1, 1]])
    colors = assets.analyze_colors(labels, [(0, 0, 0), (100, 100, 100)])

    assert assets.calculate_luminance((255, 255, 255)) == 1.0
    assert assets.assign_patterns([]) == {}
    assert assets.assign_patterns(colors) == {0: "solid_black", 1: "solid_white"}
    output = np.full((2, 2), 255, dtype=np.uint8)
    assert assets.apply_pattern(output, labels == 0, "solid_black").tolist() == [
        [0, 0],
        [255, 255],
    ]


def test_image_normalization_crop_and_fit_defensive_paths(tmp_path):
    """Small, opaque, palette, grayscale, and blank inputs should normalize safely."""
    rgb = Image.new("RGB", (4, 3), "white")
    assert assets._flatten_color_source(rgb, (255, 255, 255), spectra=False) is rgb
    grayscale = assets._flatten_color_source(Image.new("L", (4, 3)), (255, 255, 255), spectra=False)
    assert grayscale.mode == "RGB"
    palette = Image.new("P", (4, 3))
    assert assets._flatten_color_source(palette, (255, 255, 255), spectra=False).mode == "RGB"
    alpha = assets._flatten_color_source(Image.new("LA", (4, 3)), (255, 255, 255), spectra=True)
    assert alpha.mode == "RGB"
    assert assets._crop_non_white(rgb) is rgb
    assert assets._crop_non_white(Image.new("L", (2, 2), 0)).size == (2, 2)
    assert assets._fit_track(rgb) is rgb
    assert assets._fit_track(Image.new("RGB", (1000, 800))).size == (350, 280)

    blank = tmp_path / "blank.png"
    Image.new("RGB", (20, 10), "white").save(blank)
    output = tmp_path / "blank.bmp"
    assert assets.process_track_image(blank, output, "mono")["final_dimensions"] == (20, 10)

    no_pixels = SimpleNamespace(load=lambda: None, width=1, height=1)
    with pytest.raises(ValueError, match="access track pixels"):
        assets._crop_non_white(no_pixels)  # type: ignore[arg-type]


def test_color_encoding_requires_a_color_palette(tmp_path):
    """Internal color encoders should reject the monochrome spec explicitly."""
    mono = assets.get_palette_spec("mono")
    image = Image.new("RGB", (2, 2), "white")
    with pytest.raises(ValueError, match="color palette"):
        assets._map_color_palette(image, mono)
    with pytest.raises(ValueError, match="color palette"):
        assets._write_color_bmp(tmp_path / "invalid.bmp", image, mono)


def test_track_batch_filters_sources_and_normalizes_runtime_key(tmp_path):
    """Legacy sources without a manifest remain compatible with palette preprocessing."""
    source = tmp_path / "tracks"
    output = tmp_path / "output"
    source.mkdir()
    Image.new("RGB", (20, 10), "black").save(source / "vegas.png")
    Image.new("RGB", (20, 10), "red").save(source / "vegas_bwr.png")
    Image.new("RGB", (20, 10), "black").save(source / "monaco.png")

    result = assets.preprocess_tracks(
        "bwr",
        ["vegas"],
        source_dir=source,
        output_dir=output,
    )

    assert result.processed == 1
    assert result.failures == 0
    assert result.input_bytes > 0
    assert result.output_bytes > 0
    assert [path.name for path in output.iterdir()] == ["las_vegas.bmp"]
    assert assets._track_output_stem(Path("monaco_bwry.png")) == "monaco"
    assert not (source / "sources.json").exists()


def test_managed_track_bundle_is_validated_before_preprocessing(tmp_path):
    """A valid marker permits the selected managed palette source to be consumed."""
    source = _write_managed_bundle(tmp_path)
    output = tmp_path / "output"

    result = assets.preprocess_tracks(
        "bwr",
        ["managed"],
        source_dir=source,
        output_dir=output,
    )

    assert result.processed == 1
    assert (output / "managed.bmp").is_file()


@pytest.mark.parametrize("corruption", ["missing-marker", "tampered-png"])
def test_managed_track_bundle_fails_closed_before_preprocessing(tmp_path, corruption):
    """Missing commit state or changed PNG bytes must prevent any runtime output."""
    source = _write_managed_bundle(tmp_path, write_marker=corruption != "missing-marker")
    output = tmp_path / "output"
    if corruption == "tampered-png":
        with (source / "managed_bwr.png").open("ab") as handle:
            handle.write(b"tampered")

    with pytest.raises(assets.PreprocessingError, match="managed track artwork"):
        assets.preprocess_tracks(
            "bwr",
            ["managed"],
            source_dir=source,
            output_dir=output,
        )

    assert not output.exists()


@pytest.mark.parametrize("palette", assets.PREPROCESS_PALETTES)
def test_shipped_track_assets_match_source_preprocessing(tmp_path, palette):
    """Shipped runtime BMPs must stay synchronized with their preferred source art."""
    generated = tmp_path / palette

    result = assets.preprocess_tracks(palette, output_dir=generated)

    spec = assets.get_palette_spec(palette)
    shipped = assets.PROJECT_ROOT / "app" / "assets" / spec.track_output
    generated_paths = sorted(generated.glob("*.bmp"))
    mismatches = []
    for generated_path in generated_paths:
        shipped_path = shipped / generated_path.name
        if not shipped_path.is_file():
            mismatches.append(f"{generated_path.name}: missing")
        elif generated_path.read_bytes() != shipped_path.read_bytes():
            mismatches.append(f"{generated_path.name}: differs")

    assert result.processed == len(generated_paths)
    assert result.failures == 0
    assert not mismatches, (
        f"{palette} runtime track assets are stale; regenerate them with "
        f"`uv run python -m scripts.manage preprocess tracks --palette {palette}`: "
        + ", ".join(mismatches)
    )


def test_flag_batch_and_empty_input_errors(tmp_path):
    """Flag batches should write all sources and fail loudly for missing inputs."""
    source = tmp_path / "flags"
    output = tmp_path / "output"
    source.mkdir()
    Image.new("RGB", (10, 6), "red").save(source / "test.png")

    result = assets.preprocess_flags("bwry", source_dir=source, output_dir=output)

    assert result.processed == 1
    assert (output / "test.bmp").exists()
    with pytest.raises(assets.PreprocessingError, match="Input directory not found"):
        assets.preprocess_flags("bwr", source_dir=tmp_path / "missing", output_dir=output)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(assets.PreprocessingError, match="No flag images"):
        assets.preprocess_flags("bwr", source_dir=empty, output_dir=output)
    with pytest.raises(assets.PreprocessingError, match="Input directory not found"):
        assets.preprocess_tracks("bwr", source_dir=tmp_path / "missing", output_dir=output)
    with pytest.raises(assets.PreprocessingError, match="No track images"):
        assets.preprocess_tracks("bwr", source_dir=empty, output_dir=output)


def test_batch_reports_partial_failure_and_continues(tmp_path):
    """One bad source should not prevent later sources from being attempted."""
    sources = [tmp_path / "one.png", tmp_path / "two.png"]
    for source in sources:
        source.touch()
    processor = Mock(side_effect=[RuntimeError("bad source"), {"input_size": 2, "output_size": 3}])

    with pytest.raises(assets.PreprocessingError, match="1 of 2 assets failed"):
        assets._run_batch(sources, tmp_path, "bwr", lambda path: path.stem, processor)

    assert processor.call_count == 2


def test_manage_cli_routes_commands_and_reports_backend_errors(monkeypatch, capsys):
    """The public command hierarchy should route tracks, flags, and failures."""
    tracks = Mock()
    flags = Mock()
    monkeypatch.setattr(manage, "preprocess_tracks", tracks)
    monkeypatch.setattr(manage, "preprocess_flags", flags)

    assert (
        manage.main(["preprocess", "tracks", "--palette", "bwr", "--circuits", "monaco,suzuka"])
        == 0
    )
    tracks.assert_called_once_with("bwr", ["monaco", "suzuka"])
    assert manage.main(["preprocess", "flags", "--palette", "spectra6"]) == 0
    flags.assert_called_once_with("spectra6")

    tracks.side_effect = assets.PreprocessingError("broken")
    assert manage.main(["preprocess", "tracks", "--palette", "mono"]) == 1
    assert "error: broken" in capsys.readouterr().err


def test_legacy_cli_wrapper_forwards_existing_arguments(monkeypatch):
    """Transition wrappers should preserve old optional arguments and exit codes."""
    main = Mock(return_value=7)
    monkeypatch.setattr(manage, "main", main)
    monkeypatch.setattr(manage.sys, "argv", ["preprocess_tracks_bwr.py", "--circuits", "monaco"])

    with pytest.raises(SystemExit, match="7"):
        manage.legacy_main("tracks", "bwr")

    main.assert_called_once_with(
        ["preprocess", "tracks", "--palette", "bwr", "--circuits", "monaco"]
    )


@pytest.mark.parametrize(
    "script_name",
    [
        "preprocess_flags.py",
        "preprocess_flags_bwr.py",
        "preprocess_flags_bwry.py",
        "preprocess_flags_spectra6.py",
        "preprocess_tracks.py",
        "preprocess_tracks_bwr.py",
        "preprocess_tracks_bwry.py",
        "preprocess_tracks_spectra6.py",
    ],
)
def test_legacy_cli_wrappers_run_directly_by_filename(script_name, tmp_path):
    """Every compatibility filename should bootstrap imports outside the repository."""
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", str(repository_root / "scripts" / script_name), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
