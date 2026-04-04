"""Tests for shared template helpers."""

from app.web.templates import format_bytes


def test_format_bytes_uses_decimal_thresholds():
    assert format_bytes(999) == "999 B"
    assert format_bytes(1000) == "1.0 KB"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(1_000_000) == "1.00 MB"
    assert format_bytes(1_000_000_000) == "1.00 GB"
