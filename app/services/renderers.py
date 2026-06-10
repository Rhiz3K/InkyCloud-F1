"""Single factory for display-mode renderers.

The display->renderer mapping used to be duplicated in routes/images.py,
routes/previews.py, and twice in services/scheduler.py; a palette-specific fix
applied to one copy (like the BWR results-flag map) silently missed the others.
"""

from __future__ import annotations

from app.services.bwr_renderer import BwrRenderer
from app.services.bwry_renderer import BwryRenderer
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer

AnyRenderer = Renderer | Spectra6Renderer | BwrRenderer | BwryRenderer


def create_renderer(display: str, translator: dict, lang: str) -> AnyRenderer:
    """Instantiate the renderer for the requested display mode ("1bit" fallback)."""
    if display == "spectra6":
        return Spectra6Renderer(translator, lang)
    if display == "bwr":
        return BwrRenderer(translator, lang)
    if display == "bwry":
        return BwryRenderer(translator, lang)
    return Renderer(translator, lang)
