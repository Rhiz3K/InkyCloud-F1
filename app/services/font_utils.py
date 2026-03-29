"""Font loading helpers for image renderers."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"

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


def load_ui_font(
    lang_code: str, size: int, *, bold: bool = False
) -> FreeTypeFont | ImageFont.ImageFont:
    """Load the main UI font with locale-aware fallbacks."""
    if lang_code in _CJK_FACE_INDEX:
        cjk_font = _load_cjk_font(lang_code, size)
        if cjk_font is not None:
            return cjk_font

    font_filename = "TitilliumWeb-Bold.ttf" if bold else "TitilliumWeb-Regular.ttf"
    font_path = FONTS_DIR / font_filename
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception as exc:
            logger.warning("Failed to load TitilliumWeb %s: %s", font_path.name, exc)

    fallback_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(fallback_name, size)
    except OSError:
        return ImageFont.load_default()


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


def _load_cjk_font(lang_code: str, size: int) -> FreeTypeFont | ImageFont.ImageFont | None:
    """Load a bundled or system CJK font for Japanese and Simplified Chinese."""
    face_index = _CJK_FACE_INDEX[lang_code]
    for font_path in _CJK_FONT_FILES["regular"]:
        if not font_path.exists():
            continue
        try:
            return ImageFont.truetype(str(font_path), size, index=face_index)
        except Exception as exc:
            logger.warning("Failed to load CJK font %s (index %s): %s", font_path, face_index, exc)
    return None
