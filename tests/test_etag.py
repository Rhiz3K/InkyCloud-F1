"""Tests for strong BMP ETags and mtime-bound sidecars."""

import os

import pytest

from app.utils.etag import (
    encode_etag_sidecar,
    etag_sidecar_path,
    if_none_match_matches,
    read_etag_sidecar,
    strong_etag,
)


def test_strong_etag_is_stable_and_changes_with_content():
    first = strong_etag(b"bmp")

    assert first == strong_etag(b"bmp")
    assert first != strong_etag(b"changed")
    assert first.startswith('"') and first.endswith('"')


def test_sidecar_encoder_rejects_non_strong_etag():
    with pytest.raises(ValueError, match="strong SHA-256 ETag"):
        encode_etag_sidecar(1, 'W/"weak"')


def test_sidecar_is_valid_only_for_matching_bmp_mtime(tmp_path):
    image_path = tmp_path / "calendar.bmp"
    image_path.write_bytes(b"bmp")
    etag = strong_etag(b"bmp")
    original_mtime_ns = image_path.stat().st_mtime_ns
    etag_sidecar_path(image_path).write_bytes(encode_etag_sidecar(original_mtime_ns, etag))

    assert read_etag_sidecar(image_path) == etag

    image_path.write_bytes(b"changed")
    os.utime(image_path, ns=(original_mtime_ns + 1, original_mtime_ns + 1))
    assert read_etag_sidecar(image_path) is None


def test_if_none_match_accepts_lists_wildcards_and_weak_form():
    etag = strong_etag(b"bmp")

    assert if_none_match_matches(f'"other", {etag}', etag)
    assert if_none_match_matches("*", etag)
    assert if_none_match_matches(f"W/{etag}", etag)
    assert not if_none_match_matches('"other"', etag)
