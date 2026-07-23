"""Tests for downloaded and generated image asset safety."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

from app.services.asset_preprocessing import assign_patterns
from app.utils.image_assets import atomic_save_image, decode_image_bytes
from scripts import download_flags


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 3), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_decode_image_bytes_rejects_invalid_payload():
    with pytest.raises(Exception):
        decode_image_bytes(b"not an image", expected_format="PNG")


def test_atomic_save_image_writes_valid_image(tmp_path):
    output_path = tmp_path / "asset.png"

    atomic_save_image(output_path, Image.new("RGB", (5, 4), "blue"), image_format="PNG")

    decoded = decode_image_bytes(output_path.read_bytes(), expected_format="PNG")
    assert decoded.size == (5, 4)


def test_flag_download_does_not_replace_asset_with_invalid_bytes(tmp_path, monkeypatch):
    output_path = tmp_path / "gb.png"
    original = _png_bytes()
    output_path.write_bytes(original)
    monkeypatch.setattr(
        download_flags.httpx,
        "get",
        Mock(return_value=Mock(status_code=200, content=b"upstream error page")),
    )

    assert download_flags.download_flat_flag("gb", output_path) is False
    assert output_path.read_bytes() == original


@pytest.mark.parametrize(
    ("luminance", "expected"),
    [(0.9, "solid_white"), (0.1, "solid_black")],
)
def test_single_color_flag_assignment_does_not_overwrite_itself(luminance, expected):
    colors = [{"index": 0, "luminance": luminance, "area": 1.0}]

    assert assign_patterns(colors) == {0: expected}
