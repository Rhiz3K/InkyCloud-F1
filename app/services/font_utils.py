"""Font loading helpers for image renderers."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"

# Rendering runs in a thread pool (asyncio.to_thread), so fonts are cached PER THREAD rather
# than globally: a PIL FreeTypeFont is not guaranteed safe to use from multiple threads at once,
# but per-thread reuse still avoids reopening the font file on every fit_ui_font size probe.
_thread_local = threading.local()


_FONT_CACHE_MAXSIZE = 256


def _cached_truetype(path: str, size: int, *, index: int = 0) -> FreeTypeFont:
    """Return a per-thread cached TrueType font, loading it on first use.

    fit_ui_font probes many sizes, so the cache is bounded: once it exceeds
    _FONT_CACHE_MAXSIZE entries it is cleared to avoid unbounded growth.
    """
    cache = getattr(_thread_local, "fonts", None)
    if cache is None:
        cache = {}
        _thread_local.fonts = cache
    key = (path, size, index)
    font = cache.get(key)
    if font is None:
        if len(cache) >= _FONT_CACHE_MAXSIZE:
            cache.clear()
        font = ImageFont.truetype(path, size, index=index)
        cache[key] = font
    return font


_CJK_FONT_FILES = {
    "regular": (
        FONTS_DIR / "NotoSansCJK-Regular.ttc",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ),
}

_CJK_FACE_INDEX = {
    "ja": 0,
    "zh-CN": 2,
}

CJK_LANG_CODES = frozenset(_CJK_FACE_INDEX)


def load_brand_font(size: int, *, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the default Latin UI font without locale-specific CJK substitution."""
    font_filename = "TitilliumWeb-Bold.ttf" if bold else "TitilliumWeb-Regular.ttf"
    font_path = FONTS_DIR / font_filename
    if font_path.exists():
        try:
            return _cached_truetype(str(font_path), size)
        except OSError as exc:
            logger.warning("Failed to load TitilliumWeb %s: %s", font_path.name, exc)

    fallback_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return _cached_truetype(fallback_name, size)
    except OSError:
        return ImageFont.load_default()


def load_ui_font(
    lang_code: str, size: int, *, bold: bool = False
) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the main UI font with locale-aware fallbacks."""
    if lang_code in _CJK_FACE_INDEX:
        cjk_font = _load_cjk_font(lang_code, size)
        if cjk_font is not None:
            return cjk_font

    return load_brand_font(size, bold=bold)


def fit_ui_font(
    draw: ImageDraw.ImageDraw,
    lang_code: str,
    text: str,
    *,
    max_width: int,
    base_size: int,
    min_size: int,
    bold: bool = False,
) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the largest UI font that fits into the provided width."""
    for size in range(base_size, min_size - 1, -1):
        font = load_ui_font(lang_code, size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            return font
    return load_ui_font(lang_code, min_size, bold=bold)


def fit_ui_font_box(
    draw: ImageDraw.ImageDraw,
    lang_code: str,
    text: str,
    *,
    max_width: int,
    max_height: int,
    base_size: int,
    min_size: int,
    bold: bool = False,
) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the largest UI font that fits the provided width and height box."""
    for size in range(base_size, min_size - 1, -1):
        font = load_ui_font(lang_code, size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            return font
    return load_ui_font(lang_code, min_size, bold=bold)


def fit_brand_font_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_height: int,
    base_size: int,
    min_size: int,
    bold: bool = False,
) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the largest non-CJK UI font that fits the provided width and height box."""
    for size in range(base_size, min_size - 1, -1):
        font = load_brand_font(size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            return font
    return load_brand_font(min_size, bold=bold)


def _load_cjk_font(lang_code: str, size: int) -> FreeTypeFont | ImageFont.ImageFont | None:
    """Load a bundled or system CJK font for Japanese and Simplified Chinese."""
    face_index = _CJK_FACE_INDEX[lang_code]
    for font_path in _CJK_FONT_FILES["regular"]:
        if not font_path.exists():
            continue
        try:
            return _cached_truetype(str(font_path), size, index=face_index)
        except Exception as exc:
            logger.warning("Failed to load CJK font %s (index %s): %s", font_path, face_index, exc)
    return None
