"""Internationalization (i18n) service."""

import json
import logging
from pathlib import Path
from typing import Dict

from app.config import config

logger = logging.getLogger(__name__)

# Cache for loaded translations
_translations_cache: Dict[str, dict] = {}

# Valid language codes (allowlist to prevent path injection)
VALID_LANGUAGES = {"en", "cs"}


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
        logger.warning(f"Invalid language '{lang}', falling back to {config.DEFAULT_LANG}")
        lang = config.DEFAULT_LANG

    # Return cached translations if available
    if lang in _translations_cache:
        return _translations_cache[lang]

    # Load translations from file
    translations_dir = Path(__file__).parent.parent.parent / "translations"
    translation_file = translations_dir / f"{lang}.json"

    try:
        if translation_file.exists():
            with open(translation_file, "r", encoding="utf-8") as f:
                translations = json.load(f)
                _translations_cache[lang] = translations
                logger.info(f"Loaded translations for language: {lang}")
                return translations
        else:
            logger.warning(f"Translation file not found: {translation_file}")
            # Return default English translations
            return get_translator(config.DEFAULT_LANG)

    except json.JSONDecodeError as e:
        logger.error(f"Error parsing translation file {translation_file}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error loading translations: {str(e)}")
        return {}
