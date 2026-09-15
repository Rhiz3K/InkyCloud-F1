"""Reviewed circuit outlines, attribution and public artwork option identifiers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
from typing import Any

from app.paths import ASSETS_DIR
from app.services.circuit_metadata import canonical_circuit_id
from app.services.i18n import get_translator

CATALOG_PATH = ASSETS_DIR / "track_art" / "catalog.json"
# Change the renderer version whenever drawing or fitting changes. The catalogue
# digest also invalidates calendar/pre-generation caches when a source changes.
ARTWORK_VERSION = "open-tracks-3-" + sha256(CATALOG_PATH.read_bytes()).hexdigest()[:16]


class TrackStyle(StrEnum):
    """Stable style identifiers shared by the UI, calendar and standalone API."""

    RELIEF = "relief"
    POSTER = "poster"
    CLEAN = "clean"
    LINO = "lino"
    RISO = "riso"
    SCREENPRINT = "screenprint"
    CONTOURS = "contours"
    GEOMETRIC = "geometric"
    BLUEPRINT = "blueprint"
    RIBBON = "ribbon"
    MOSAIC = "mosaic"
    HALFTONE = "halftone"
    NEGATIVE = "negative"
    PAPERCUT = "papercut"
    TYPOGRAPHIC = "typographic"
    ARTDECO = "artdeco"
    STENCIL = "stencil"
    PIXEL = "pixel"


class TrackSource(StrEnum):
    """Locally reviewed source families; never arbitrary remote SVG URLs."""

    JULES = "jules"
    COMMONS = "commons"


class TrackAccent(StrEnum):
    """Every subset of the four chromatic ePaper accents."""

    ALL = "all"
    MONO = "mono"
    RED = "red"
    YELLOW = "yellow"
    BLUE = "blue"
    GREEN = "green"
    WARM = "warm"
    RED_BLUE = "red-blue"
    RED_GREEN = "red-green"
    YELLOW_BLUE = "yellow-blue"
    YELLOW_GREEN = "yellow-green"
    COOL = "cool"
    RED_YELLOW_BLUE = "red-yellow-blue"
    RED_YELLOW_GREEN = "red-yellow-green"
    RED_BLUE_GREEN = "red-blue-green"
    YELLOW_BLUE_GREEN = "yellow-blue-green"


@dataclass(frozen=True, slots=True)
class TrackOptions:
    """Immutable per-request artwork selection, safe to share across render threads."""

    style: TrackStyle = TrackStyle.RELIEF
    source: TrackSource = TrackSource.JULES
    accent: TrackAccent = TrackAccent.ALL

    @property
    def cache_key(self) -> str:
        """Identify all artwork inputs without mixing concurrently requested variants."""
        return f"{ARTWORK_VERSION}:{self.style}:{self.source}:{self.accent}"


DEFAULT_TRACK_OPTIONS = TrackOptions()
COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "yellow": "#FFD800",
    "blue": "#00A8FF",
    "green": "#00D800",
}
DISPLAY_COLORS = {
    "1bit": ("black", "white"),
    "bwr": ("black", "white", "red"),
    "bwry": ("black", "white", "red", "yellow"),
    "spectra6": ("black", "white", "red", "yellow", "blue", "green"),
}
ACCENTS = {accent.value: tuple(accent.value.split("-")) for accent in TrackAccent} | {
    "all": ("red", "yellow", "blue", "green"),
    "mono": (),
    "warm": ("red", "yellow"),
    "cool": ("blue", "green"),
}
STYLE_LABELS = {
    "relief": ("Isometric relief", "Izometrický reliéf"),
    "poster": ("Poster", "Plakát"),
    "clean": ("Clean outline", "Čistý obrys"),
    "lino": ("Linocut", "Linoryt"),
    "riso": ("Risograph", "Risograf"),
    "screenprint": ("Screen print", "Sítotisk"),
    "contours": ("Outline echoes", "Ozvěny obrysu"),
    "geometric": ("Geometric collage", "Geometrická koláž"),
    "blueprint": ("Blueprint", "Technický nákres"),
    "ribbon": ("Ribbon", "Stuha"),
    "mosaic": ("Mosaic", "Mozaika"),
    "halftone": ("Halftone", "Polotón"),
    "negative": ("Negative", "Negativ"),
    "papercut": ("Paper cut", "Papírový výřez"),
    "typographic": ("Typography", "Typografie"),
    "artdeco": ("Art deco", "Art deco"),
    "stencil": ("Stencil", "Šablona"),
    "pixel": ("Pixel art", "Pixel art"),
}


@lru_cache(maxsize=1)
def load_track_catalog() -> dict[str, Any]:
    """Load the bundled, pinned catalogue once; consumers must not mutate it."""
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def track_source(circuit_id: str, source: TrackSource) -> dict[str, Any] | None:
    """Find a reviewed outline without filesystem interpolation or legacy fallback."""
    circuit_id = canonical_circuit_id(circuit_id)
    return load_track_catalog()["geometries"].get(f"{circuit_id}:{source}")


def track_credit(circuit_id: str, source: TrackSource) -> dict[str, Any] | None:
    """Return public provenance, keeping the bulky drawing instructions internal."""
    geometry = track_source(circuit_id, source)
    if geometry is None:
        return None
    return {
        key: value for key, value in geometry.items() if key not in {"d", "commands", "transform"}
    }


def effective_accents(display: str, accent: TrackAccent) -> tuple[str, ...]:
    """Intersect the requested accents with hardware colours; never substitute hues."""
    return tuple(color for color in ACCENTS[accent] if color in DISPLAY_COLORS[display])


def track_ui_options(lang: str = "en") -> dict[str, Any]:
    """Provide localised control labels and the same identifiers advertised by the API."""
    labels = get_translator(lang)["track_art"]
    names = labels["colors"]
    return {
        **{
            key: labels[key]
            for key in ("styleLabel", "sourceLabel", "accentLabel", "hint", "creditsLabel")
        },
        "styles": [
            {"id": style.value, "label": labels["styles"][style.value]} for style in TrackStyle
        ],
        "sources": [
            {"id": "jules", "label": "Jules Roy"},
            {"id": "commons", "label": "Wikimedia Commons"},
        ],
        "accents": [
            {
                "id": accent.value,
                "label": labels[accent.value]
                if accent in {TrackAccent.ALL, TrackAccent.MONO}
                else " + ".join(names[color] for color in ACCENTS[accent]),
            }
            for accent in TrackAccent
        ],
        "defaults": {"style": "relief", "source": "jules", "accent": "all"},
    }
