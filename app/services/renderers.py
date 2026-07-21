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
RendererType = type[Renderer] | type[Spectra6Renderer] | type[BwrRenderer] | type[BwryRenderer]
DISPLAY_TYPES = ("1bit", "bwr", "bwry", "spectra6")
COLOR_DISPLAYS = frozenset({"bwr", "bwry", "spectra6"})

_RENDERER_TYPES: dict[str, RendererType] = {
    "1bit": Renderer,
    "bwr": BwrRenderer,
    "bwry": BwryRenderer,
    "spectra6": Spectra6Renderer,
}


def create_renderer(display: str, translator: dict, lang: str) -> AnyRenderer:
    """Instantiate the renderer for the requested display mode ("1bit" fallback)."""
    renderer_type = _RENDERER_TYPES.get(display, Renderer)
    return renderer_type(translator, lang)
