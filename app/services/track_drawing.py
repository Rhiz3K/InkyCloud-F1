"""Native-pixel SVG artwork; every decorative layer participates in the safe crop."""

from __future__ import annotations

import json
import math
from urllib.parse import urlsplit

# Build XML nodes only; this module never parses external XML or entities.
# skipcq: BAN-B405
from xml.etree.ElementTree import Element, SubElement, tostring

from app.services.circuit_metadata import canonical_circuit_id
from app.services.track_catalog import (
    COLORS,
    TrackOptions,
    effective_accents,
    load_track_catalog,
    track_credit,
)
from app.services.track_geometry import FittedTrack, fit_track

BLACK, WHITE = COLORS["black"], COLORS["white"]


def element(
    parent: Element, tag: str, attrs: dict | None = None, text: str | None = None
) -> Element:
    """Create escaped XML from local data, retaining hyphenated SVG attributes."""
    node = SubElement(parent, tag, {k: str(v) for k, v in (attrs or {}).items()})
    node.text = text
    return node


class TrackDrawing:
    """Compose one artwork without external resources, filters or mutable global state."""

    def __init__(self, geometry: FittedTrack, options: TrackOptions, display: str):
        """Initialise native-pixel geometry and intersect the requested hardware palette."""
        self.g = geometry
        self.options = options
        self.width, self.height = geometry.width, geometry.height
        self.colors = [COLORS[name] for name in effective_accents(display, options.accent)]
        self.mono = not self.colors
        self.colors = self.colors or [BLACK]
        self.background = BLACK if options.style == "negative" else WHITE
        self.svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg"})
        self.defs = element(self.svg, "defs")
        self.counter = 0
        self.w = 3.2
        element(
            self.svg,
            "rect",
            {
                "width": self.width,
                "height": self.height,
                "fill": self.background,
                "data-layer": "paper",
            },
        )

    def path(self, parent: Element, attrs: dict, dx: float = 0, dy: float = 0) -> Element:
        """Draw the actual Bezier curve with strokes in the same native coordinates."""
        return element(
            parent,
            "path",
            {
                "d": self.g.d,
                "transform": f"translate({dx} {dy})",
                "fill": "none",
                "stroke-linecap": "round",
                "stroke-linejoin": "round",
                **attrs,
            },
        )

    def stroke(self, color: str, size: float, dx: float = 0, dy: float = 0, /, **attrs) -> None:
        """Add a coloured, patterned or shifted curve layer."""
        self.path(
            self.svg,
            {"stroke": color, "stroke-width": size, "data-layer": "decoration", **attrs},
            dx,
            dy,
        )

    def core(self, size: float, color: str = BLACK, halo: bool = True) -> None:
        """Keep a continuous readable circuit line above decorative layers."""
        if halo:
            self.stroke(self.background, size + 3.2)
        self.stroke(color, size, **{"data-layer": "track"})

    def pattern(self, kind: str, color: str, spacing: float, thick: float) -> str:
        """Create a sharp, palette-only dot, grid or hatch tile."""
        identifier = f"pattern-{self.counter}"
        self.counter += 1
        node = element(
            self.defs,
            "pattern",
            {
                "id": identifier,
                "width": spacing,
                "height": spacing,
                "patternUnits": "userSpaceOnUse",
            },
        )
        if kind == "dots":
            element(
                node, "circle", {"cx": spacing / 2, "cy": spacing / 2, "r": thick, "fill": color}
            )
        else:
            d = (
                f"M0 {spacing} V0 H{spacing}"
                if kind == "grid"
                else f"M{-spacing / 2} {spacing / 2} L{spacing / 2} {-spacing / 2} "
                f"M0 {spacing} L{spacing} 0 "
                f"M{spacing / 2} {spacing * 1.5} L{spacing * 1.5} {spacing / 2}"
            )
            element(node, "path", {"d": d, "fill": "none", "stroke": color, "stroke-width": thick})
        return f"url(#{identifier})"

    def bands(self, on_curve: bool = True) -> str:
        """Use all effective accents across the curve span, including narrow circuits."""
        identifier = f"bands-{self.counter}"
        self.counter += 1
        node = element(
            self.defs,
            "pattern",
            {
                "id": identifier,
                "width": self.width,
                "height": self.height,
                "patternUnits": "userSpaceOnUse",
            },
        )
        left = min(p[0] for p in self.g.points) - self.w if on_curve else 0
        right = max(p[0] for p in self.g.points) + self.w if on_curve else self.width
        size = (right - left) / len(self.colors)
        for i, color in enumerate(self.colors):
            element(
                node,
                "rect",
                {
                    "x": left + i * size,
                    "y": 0,
                    "width": size + 0.15,
                    "height": self.height,
                    "fill": color,
                },
            )
        return f"url(#{identifier})"

    def blocks(self, angle: float, patterned: bool = False) -> None:
        """Clip large rotated colour bands to the selected source outline."""
        clip = element(self.defs, "clipPath", {"id": "inside"})
        self.path(clip, {"fill": BLACK, "clip-rule": "evenodd"})
        inside = element(self.svg, "g", {"clip-path": "url(#inside)"})
        group = element(
            inside, "g", {"transform": f"rotate({angle} {self.width / 2} {self.height / 2})"}
        )
        rad = math.radians(-angle)
        projected = [
            (p[0] - self.width / 2) * math.cos(rad)
            - (p[1] - self.height / 2) * math.sin(rad)
            + self.width / 2
            for p in self.g.points
        ]
        count = 3 if self.mono else len(self.colors)
        start, size = min(projected), (max(projected) - min(projected)) / count
        for i in range(count):
            fill = self.colors[i % len(self.colors)]
            if self.mono:
                fill = (
                    WHITE if i % 2 else self.pattern("dots", BLACK, 7, 1.6) if patterned else BLACK
                )
            elif patterned:
                fill = self.pattern("dots", fill, 5 + i, 1.35)
            element(
                group,
                "rect",
                {
                    "x": start + i * size,
                    "y": -self.height,
                    "width": size + 0.15,
                    "height": self.height * 3,
                    "fill": fill,
                },
            )

    def draw_clean(self) -> None:
        """Draw a fine outline with a narrow coloured centre."""
        self.core(self.w * 0.82)
        if not self.mono:
            self.stroke(self.bands(), max(1.25, self.w * 0.3))

    def draw_poster(self) -> None:
        """Draw a bold poster outline with a displaced print layer."""
        self.stroke(
            BLACK if self.mono else self.bands(), self.w * 1.8, self.w * 1.25, self.w * 1.45
        )
        self.core(self.w * 1.12)

    def draw_lino(self) -> None:
        """Draw engraved hatching around a bold track."""
        self.stroke(self.pattern("hatch", self.colors[0], 5.5, 1.55), self.w * 4.5)
        self.core(self.w * 1.4)
        if len(self.colors) > 1:
            self.stroke(self.bands(), self.w * 0.4, **{"stroke-dasharray": "7 5"})

    def draw_riso(self) -> None:
        """Compose deliberately offset risograph ink layers."""
        self.stroke(
            self.pattern("dots", self.colors[0], 5.5, 1.45), self.w * 4.6, -self.w, self.w * 1.35
        )
        self.stroke(self.colors[1 % len(self.colors)], self.w * 1.6, self.w * 1.4, -self.w * 0.8)
        if len(self.colors) > 2:
            self.stroke(self.colors[2], self.w * 1.7, -self.w * 1.4, -self.w * 0.8)
        self.core(self.w * 0.9)
        if len(self.colors) > 3:
            self.stroke(self.bands(), max(1.25, self.w * 0.33))

    def draw_screenprint(self) -> None:
        """Fill the interior with solid screen-print bands."""
        self.blocks(-24)
        self.core(self.w * 1.2)

    def draw_contours(self) -> None:
        """Draw all four outline echoes, including the outermost visible stroke."""
        for ring in range(4, 0, -1):
            self.stroke(self.colors[(ring - 1) % len(self.colors)], self.w + ring * 7.1)
            self.stroke(WHITE, self.w + ring * 7.1 - 2.6)
        self.core(self.w * 0.86, halo=False)

    def draw_geometric(self) -> None:
        """Combine a circle, block and triangle behind the circuit."""
        w, h = self.width, self.height
        element(
            self.svg,
            "circle",
            {
                "cx": w * 0.73,
                "cy": h * 0.47,
                "r": min(h * 0.30, w * 0.25),
                "fill": self.pattern("hatch", BLACK, 8, 1.2)
                if self.mono
                else self.colors[1 % len(self.colors)],
            },
        )
        element(
            self.svg,
            "rect",
            {
                "x": w * 0.13,
                "y": h * 0.49,
                "width": w * 0.29,
                "height": h * 0.32,
                "fill": self.colors[0],
            },
        )
        element(
            self.svg,
            "path",
            {
                "d": f"M{w * 0.25} {h * 0.2} L{w * 0.61} {h * 0.12} L{w * 0.51} {h * 0.4} Z",
                "fill": WHITE if self.mono else self.colors[2 % len(self.colors)],
            },
        )
        if len(self.colors) > 3:
            element(
                self.svg,
                "circle",
                {"cx": w * 0.74, "cy": h * 0.47, "r": h * 0.12, "fill": self.colors[3]},
            )
        self.core(self.w * 1.1)

    def draw_blueprint(self) -> None:
        """Draw a decorative drafting grid without suggesting measured dimensions."""
        color = BLACK if self.colors[0] == COLORS["yellow"] else self.colors[0]
        element(
            self.svg,
            "rect",
            {
                "x": 2,
                "y": 2,
                "width": self.width - 4,
                "height": self.height - 4,
                "fill": self.pattern("grid", color, 22, 0.8),
            },
        )
        self.core(self.w * 1.2)
        self.stroke(WHITE, self.w * 0.55)
        if not self.mono:
            self.stroke(self.bands(), max(1.25, self.w * 0.28))

    def draw_ribbon(self) -> None:
        """Keep two coloured ribbon edges separated by a white centre."""
        self.stroke(BLACK, self.w * 2.7)
        if not self.mono:
            self.stroke(self.bands(), self.w * 1.95)
        self.stroke(WHITE, self.w * 0.95)

    def separator(self, index: int, half_length: float, color: str, thickness: float) -> None:
        """Draw a short line perpendicular to a sampled decorative boundary."""
        points = self.g.points
        x, y = points[index]
        before, after = points[(index - 3) % len(points)], points[(index + 3) % len(points)]
        dx, dy = after[0] - before[0], after[1] - before[1]
        length = math.hypot(dx, dy) or 1
        nx, ny = -dy / length * half_length, dx / length * half_length
        element(
            self.svg,
            "path",
            {
                "d": f"M{x - nx} {y - ny} L{x + nx} {y + ny}",
                "fill": "none",
                "stroke": color,
                "stroke-width": thickness,
                "data-layer": "decorative-separator",
            },
        )

    def draw_mosaic(self) -> None:
        """Split the decorative ribbon into twelve tiles with visible separators."""
        self.stroke(BLACK, self.w * 2.6)
        colors = [BLACK, WHITE] if self.mono else self.colors
        points, n = self.g.points, len(self.g.points)
        for i in range(12):
            section = [points[j % n] for j in range(i * n // 12, (i + 1) * n // 12 + 1)]
            d = " ".join(f"{'L' if j else 'M'}{x:.4f} {y:.4f}" for j, (x, y) in enumerate(section))
            element(
                self.svg,
                "path",
                {
                    "d": d,
                    "fill": "none",
                    "stroke": colors[i % len(colors)],
                    "stroke-width": self.w * 1.85,
                    "stroke-linejoin": "round",
                },
            )
        for i in range(12):
            self.separator(i * n // 12, self.w * 1.28, BLACK, 1.5)

    def draw_halftone(self) -> None:
        """Fill the outline using coloured dots instead of continuous gradients."""
        self.blocks(18, patterned=True)
        self.core(self.w * 1.3)

    def draw_negative(self) -> None:
        """Draw white track ink on black paper with a displaced accent layer."""
        paint = self.pattern("dots", WHITE, 5, 1.1) if self.mono else self.bands()
        self.stroke(paint, self.w * 2.4, self.w * 1.4, self.w * 1.3)
        self.core(self.w * 1.1, WHITE)

    def draw_papercut(self) -> None:
        """Cut the original outline through offset paper sheets using luminance masks."""
        for layer in reversed(range(max(3, len(self.colors)))):
            shift = layer * 3.3
            identifier = f"cut-{layer}"
            mask = element(
                self.defs,
                "mask",
                {
                    "id": identifier,
                    "maskUnits": "userSpaceOnUse",
                    "maskContentUnits": "userSpaceOnUse",
                    "x": 0,
                    "y": 0,
                    "width": self.width,
                    "height": self.height,
                    "mask-type": "luminance",
                },
            )
            element(mask, "rect", {"width": self.width, "height": self.height, "fill": WHITE})
            self.path(
                mask,
                {
                    "fill": BLACK,
                    "fill-rule": "evenodd",
                    "stroke": BLACK,
                    "stroke-width": self.w * 0.6,
                },
                shift,
                shift,
            )
            fill = (
                (WHITE if layer % 2 else BLACK)
                if self.mono
                else self.colors[layer % len(self.colors)]
            )
            element(
                self.svg,
                "rect",
                {
                    "x": 1 + shift,
                    "y": 1 + shift,
                    "width": self.width - 13,
                    "height": self.height - 13,
                    "fill": fill,
                    "stroke": BLACK,
                    "stroke-width": 1.1,
                    "mask": f"url(#{identifier})",
                },
            )
        self.core(self.w * 0.68, halo=False)

    def draw_relief(self) -> None:
        """Extrude an isometric projection by seventeen purely decorative pixels."""
        self.stroke(self.pattern("hatch", BLACK, 6, 1.1), self.w * 3.8, 4, 22)
        for z in range(17, 0, -1):
            self.stroke(WHITE if z == 7 else BLACK, self.w * 2.6, 0, z)
        self.stroke(BLACK, self.w * 2.7)
        self.stroke(WHITE if self.mono else self.bands(), self.w * 1.85)

    def draw_typographic(self, lettering: str) -> None:
        """Interweave the curve with locally rendered, bounded circuit lettering."""
        w, h = self.width, self.height
        size = min(w * 0.165, (w - 28) / (len(lettering) * 0.65), h * 0.7)
        attrs = {
            "x": w / 2,
            "y": h * 0.62,
            "text-anchor": "middle",
            "font-family": "Titillium Web",
            "font-size": size,
            "font-weight": 400,
            "textLength": w - 28,
            "lengthAdjust": "spacingAndGlyphs",
        }
        element(
            self.svg, "text", {**attrs, "transform": "translate(3 4)", "fill": BLACK}, lettering
        )
        element(
            self.svg,
            "text",
            {
                **attrs,
                "fill": BLACK if self.mono else self.bands(False),
                "stroke": WHITE,
                "stroke-width": 1.4,
                "paint-order": "stroke",
            },
            lettering,
        )
        self.core(self.w * 1.25)

    def draw_artdeco(self) -> None:
        """Constrain decorative fan rays to a complete double frame."""
        w, h = self.width, self.height
        clip = element(self.defs, "clipPath", {"id": "deco-frame"})
        element(clip, "rect", {"x": 5, "y": 5, "width": w - 10, "height": h - 10})
        fan = element(self.svg, "g", {"clip-path": "url(#deco-frame)"})
        cx, cy, radius = w / 2, h - 6, w + h
        for ray in range(13):
            theta, next_theta = math.radians(205 + ray * 10), math.radians(210.5 + ray * 10)
            element(
                fan,
                "path",
                {
                    "d": f"M{cx} {cy} "
                    f"L{cx + math.cos(theta) * radius} {cy + math.sin(theta) * radius} "
                    f"L{cx + math.cos(next_theta) * radius} {cy + math.sin(next_theta) * radius} Z",
                    "fill": self.colors[ray % len(self.colors)],
                },
            )
        for inset in (1, 5):
            element(
                self.svg,
                "rect",
                {
                    "x": inset,
                    "y": inset,
                    "width": w - 2 * inset,
                    "height": h - 2 * inset,
                    "fill": "none",
                    "stroke": BLACK,
                    "stroke-width": 1.8 if inset == 1 else 1,
                },
            )
        self.core(self.w * 1.25)

    def draw_stencil(self) -> None:
        """Add eight stencil bridges around an uninterrupted circuit line."""
        self.stroke(BLACK, self.w * 4.5)
        if not self.mono:
            self.stroke(self.bands(), self.w * 3.75)
        for bridge in range(8):
            self.separator(
                int((bridge / 8 + 0.035) * len(self.g.points)) % len(self.g.points),
                self.w * 2.4,
                WHITE,
                3.6,
            )
        self.core(self.w * 0.83)

    def draw_pixel(self) -> None:
        """Join three-pixel grid cells with Bresenham lines and an eight-neighbour outline."""
        cells: dict[tuple[int, int], float] = {}
        snapped = [(int(x // 3), int(y // 3)) for x, y in self.g.points]
        for i, ((x, y), (last_x, last_y)) in enumerate(zip(snapped, (*snapped[1:], snapped[0]))):
            dx, dy = abs(last_x - x), -abs(last_y - y)
            sx, sy = (1 if x < last_x else -1), (1 if y < last_y else -1)
            error = dx + dy
            while True:
                cells.setdefault((x, y), i / len(snapped))
                if x == last_x and y == last_y:
                    break
                doubled = 2 * error
                if doubled >= dy:
                    error += dy
                    x += sx
                if doubled <= dx:
                    error += dx
                    y += sy
        outline = sorted(
            {(x + dx, y + dy) for x, y in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        )
        group = element(self.svg, "g", {"shape-rendering": "crispEdges"})
        for x, y in outline:
            element(group, "rect", {"x": x * 3, "y": y * 3, "width": 3, "height": 3, "fill": BLACK})
        for (x, y), fraction in cells.items():
            element(
                group,
                "rect",
                {
                    "x": x * 3,
                    "y": y * 3,
                    "width": 3,
                    "height": 3,
                    "fill": WHITE if self.mono else self.colors[int(fraction * len(self.colors))],
                },
            )


def build_track_svg(
    circuit_id: str,
    options: TrackOptions,
    display: str,
    width: int = 494,
    height: int = 271,
    *,
    credit_url: str = "",
) -> bytes:
    """Build an attributed SVG; standalone exports also carry a visible credit link."""
    circuit_id = canonical_circuit_id(circuit_id)
    geometry = fit_track(circuit_id, options.source, options.style, width, height)
    drawing = TrackDrawing(geometry, options, display)
    track = {item["id"]: item for item in load_track_catalog()["tracks"]}[circuit_id]
    if options.style == "typographic":
        drawing.draw_typographic(track["lettering"])
    else:
        getattr(drawing, f"draw_{options.style}")()
    credit = track_credit(circuit_id, options.source)
    assert credit is not None
    element(drawing.svg, "title", text=f"{track['name']} — {options.style}")
    element(drawing.svg, "desc", text=credit["adaptation"])
    element(drawing.svg, "metadata", text=json.dumps(credit, ensure_ascii=False))
    total_height = geometry.height
    if credit_url:
        total_height += 14
        element(
            drawing.svg,
            "rect",
            {"y": geometry.height, "width": geometry.width, "height": 14, "fill": WHITE},
        )
        link = element(drawing.svg, "a", {"href": credit_url})
        # Full per-file authorship (including upstream authors) lives at the durable
        # credit URL and in SVG metadata / PNG iTXt / HTTP Link headers.
        short_url = f"{urlsplit(credit_url).netloc}/credits"
        text = f"Map: {credit['author']} · {credit['license_label']} · {short_url}"
        element(
            link,
            "text",
            {
                "x": 2,
                "y": total_height - 3,
                "fill": BLACK,
                "font-size": 11,
                "font-family": "Titillium Web",
                "textLength": min(geometry.width - 4, len(text) * 5.2),
                "lengthAdjust": "spacingAndGlyphs",
            },
            text,
        )
    drawing.svg.attrib.update(
        width=str(geometry.width),
        height=str(total_height),
        viewBox=f"0 0 {geometry.width} {total_height}",
    )
    return tostring(drawing.svg, encoding="utf-8", xml_declaration=True)
