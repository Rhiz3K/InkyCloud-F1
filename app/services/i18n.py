"""Internationalization (i18n) service."""

import json
import logging
from pathlib import Path
from typing import Dict

from app.config import VALID_LANGUAGES, config

logger = logging.getLogger(__name__)

# Cache for loaded translations
_translations_cache: Dict[str, dict] = {}

# Pre-defined translation file paths (prevents path injection by avoiding user input in paths)
_TRANSLATIONS_DIR = Path(__file__).parent.parent.parent / "translations"
_TRANSLATION_FILES: Dict[str, Path] = {
    "en": _TRANSLATIONS_DIR / "en.json",
    "cs": _TRANSLATIONS_DIR / "cs.json",
}


def get_translator(lang: str) -> dict:
    """
    Get translation dictionary for the specified language.

    Args:
        lang: Language code (e.g., 'cs', 'en')

    Returns:
        Dictionary with translations
    """
    # Validate language against allowlist (prevents path injection)
    if lang not in VALID_LANGUAGES:
        logger.warning(
            f"Invalid language '{lang}', falling back to {config.DEFAULT_LANG}"
        )
        lang = config.DEFAULT_LANG

    # Return cached translations if available
    if lang in _translations_cache:
        return _translations_cache[lang]

    # Get pre-defined translation file path (no user input in path construction)
    translation_file = _TRANSLATION_FILES.get(lang)
    if translation_file is None:
        logger.error(f"No translation file defined for language: {lang}")
        return {}

    try:
        if translation_file.exists():
            with open(translation_file, encoding="utf-8") as f:
                translations = json.load(f)
                _translations_cache[lang] = translations
                logger.info(f"Loaded translations for language: {lang}")
                return translations
        else:
            logger.warning(f"Translation file not found: {translation_file}")
            return get_translator(config.DEFAULT_LANG)

    except json.JSONDecodeError as e:
        logger.error(f"Error parsing translation file {translation_file}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error loading translations: {str(e)}")
        return {}
