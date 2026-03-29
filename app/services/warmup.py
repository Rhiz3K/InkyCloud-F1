"""Startup warmup helpers for expensive renderer assets."""

from __future__ import annotations

import logging

from app.config import config
from app.services.bwr_renderer import BwrRenderer
from app.services.bwry_renderer import BwryRenderer
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer

logger = logging.getLogger(__name__)


def warm_teams_renderer_assets(lang: str | None = None) -> None:
    """Preload teams renderer assets so the first request avoids logo warmup."""
    resolved_lang = lang or config.DEFAULT_LANG
    translator = get_translator(resolved_lang)
    renderers = (
        Renderer(translator, resolved_lang),
        Spectra6Renderer(translator, resolved_lang),
        BwrRenderer(translator, resolved_lang),
        BwryRenderer(translator, resolved_lang),
    )

    for renderer in renderers:
        renderer._ensure_teams_assets()

    logger.info("Warmed teams renderer assets for %s", resolved_lang)
