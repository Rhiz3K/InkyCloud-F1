"""Internationalization (i18n) service."""

import json
import logging
from pathlib import Path
from typing import Dict

from app.config import VALID_LANGUAGES, config

logger = logging.getLogger(__name__)

# Cache for loaded translations
_translations_cache: Dict[str, dict] = {}


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

    # Resolve paths and verify containment (defense in depth against path traversal)
    resolved_dir = translations_dir.resolve()
    resolved_file = translation_file.resolve()
    if not str(resolved_file).startswith(str(resolved_dir)):
        logger.error(f"Path traversal attempt detected for language: {lang}")
        return {}

    try:
        if resolved_file.exists():
            with open(resolved_file, "r", encoding="utf-8") as f:
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
