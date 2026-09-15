"""Fit complete circuit ink to a display using a global convex-hull rotation search."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from app.services.track_catalog import TrackSource, TrackStyle, track_source

type Point = tuple[float, float]
type Matrix = tuple[float, float, float, float, float, float]
INK_MARGINS = {
    "clean": (4, 4, 4, 4),
    "poster": (5, 5, 9, 10),
    "lino": (9, 9, 9, 9),
    "riso": (13, 10, 10, 13),
    "screenprint": (5, 5, 5, 5),
    "contours": (17, 17, 17, 17),
    "geometric": (5, 5, 5, 5),
    "blueprint": (5, 5, 5, 5),
    "ribbon": (6, 6, 6, 6),
    "mosaic": (6, 6, 6, 6),
    "halftone": (5, 5, 5, 5),
    "negative": (5, 5, 10, 10),
    "papercut": (3, 3, 14, 14),
    "relief": (9, 9, 12, 30),
    "typographic": (5, 5, 5, 5),
    "artdeco": (5, 5, 5, 5),
    "stencil": (11, 11, 11, 11),
    "pixel": (7, 7, 7, 7),
}


@dataclass(frozen=True, slots=True)
class FittedTrack:
    """A native-pixel curve, its decorative sampling and auditable fit parameters."""

    width: int
    height: int
    d: str
    points: tuple[Point, ...]
    matrix: Matrix
    angle: float
    scale: float
    gain: float
    reserve: float


def transform(point: Point, matrix: Matrix) -> Point:
    """Apply an SVG affine transform without scaling stroke widths."""
    x, y = point
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _distance_to_segment(point: Point, first: Point, last: Point) -> float:
    """Bound a Bezier control point's distance from the finite endpoint segment."""
    dx, dy = last[0] - first[0], last[1] - first[1]
    denominator = dx * dx + dy * dy
    fraction = (
        max(0, min(1, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator))
        if denominator
        else 0
    )
    return math.hypot(point[0] - first[0] - fraction * dx, point[1] - first[1] - fraction * dy)


def _flatten(curve: tuple[Point, ...], tolerance: float, output: list[Point]) -> None:
    """Subdivide with de Casteljau until the entire control hull fits a safe capsule."""
    if (
        max((_distance_to_segment(p, curve[0], curve[-1]) for p in curve[1:-1]), default=0)
        <= tolerance
    ):
        output.append(curve[-1])
        return
    levels = [curve]
    while len(levels[-1]) > 1:
        previous = levels[-1]
        levels.append(
            tuple(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(previous, previous[1:]))
        )
    _flatten(tuple(level[0] for level in levels), tolerance, output)
    _flatten(tuple(level[-1] for level in reversed(levels)), tolerance, output)


@lru_cache(maxsize=50)
def source_points(circuit_id: str, source: TrackSource) -> tuple[tuple[Point, ...], float]:
    """Flatten only reviewed commands, retaining a conservative curve-error reserve."""
    geometry = track_source(circuit_id, source)
    if geometry is None:
        raise ValueError("No reviewed circuit outline")
    matrix = tuple(geometry["transform"])
    commands = [
        (
            command[0],
            tuple(
                transform((command[i], command[i + 1]), matrix) for i in range(1, len(command), 2)
            ),
        )
        for command in geometry["commands"]
    ]
    controls = [point for _, points in commands for point in points]
    span = max(max(p[i] for p in controls) - min(p[i] for p in controls) for i in (0, 1))
    tolerance = span / 20000
    points: list[Point] = []
    for command, coordinates in commands:
        if command == "M":
            points.append(coordinates[0])
        elif command == "Z":
            points.append(points[0])
        else:
            _flatten((points[-1], *coordinates), tolerance, points)
    return tuple(points), tolerance + span * 1e-8


def convex_hull(points: tuple[Point, ...]) -> tuple[Point, ...]:
    """Compute a monotone-chain hull without interior or duplicate vertices."""
    ordered = sorted(set(points))
    halves: list[list[Point]] = []
    for sequence in (ordered, list(reversed(ordered))):
        half: list[Point] = []
        for p in sequence:
            while len(half) > 1:
                a, b = half[-2:]
                if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0:
                    break
                half.pop()
            half.append(p)
        halves.append(half[:-1])
    return tuple(halves[0] + halves[1])


def _extent(hull: tuple[Point, ...], angle: float, reserve: float) -> tuple:
    """Return padded rotated bounds and the source vertices defining each extreme."""
    c, s = math.cos(angle), math.sin(angle)
    xs = [p[0] * c - p[1] * s for p in hull]
    ys = [p[0] * s + p[1] * c for p in hull]
    indices = (
        min(range(len(hull)), key=xs.__getitem__),
        max(range(len(hull)), key=xs.__getitem__),
        min(range(len(hull)), key=ys.__getitem__),
        max(range(len(hull)), key=ys.__getitem__),
    )
    return (
        min(xs) - reserve,
        max(xs) + reserve,
        min(ys) - reserve,
        max(ys) + reserve,
        *(hull[i] for i in indices),
    )


def best_rotation(
    points: tuple[Point, ...], reserve: float, width: float, height: float
) -> tuple[float, float, tuple, float]:
    """Maximise uniform scale, enumerating all hull events and constraint crossings.

    On each event interval the two extents are sinusoidal plus 2*reserve. A maximum
    occurs at an endpoint, a stationary extent, or a crossing of width/height limits.
    This optimises the display fit, which differs from a minimum-area rectangle.
    """
    hull = convex_hull(points)
    events = {0.0, math.pi}
    for p, q in zip(hull, (*hull[1:], hull[0]), strict=True):
        edge = math.atan2(q[1] - p[1], q[0] - p[0])
        events.update(((-edge) % math.pi, (math.pi / 2 - edge) % math.pi))
    cuts = sorted(events)
    candidates = list(cuts)
    for lo, hi in zip(cuts, cuts[1:]):
        _, _, _, _, lx, hx, ly, hy = _extent(hull, (lo + hi) / 2, reserve)
        a, b, d, e = hx[0] - lx[0], -(hx[1] - lx[1]), hy[0] - ly[0], hy[1] - ly[1]
        candidates.extend(
            root
            for value in (math.atan2(b, a), math.atan2(d, e))
            if lo < (root := value % math.pi) < hi
        )
        p, q = height * a - width * e, height * b - width * d
        radius, target = math.hypot(p, q), 2 * reserve * (width - height)
        if radius and abs(target) <= radius:
            phase = math.atan2(q, p)
            offset = math.acos(max(-1, min(1, target / radius)))
            for value in (phase + offset, phase - offset):
                candidates.extend(
                    root for turn in range(-2, 3) if lo < (root := value + turn * 2 * math.pi) < hi
                )
    initial = _extent(hull, 0, reserve)
    initial_scale = min(width / (initial[1] - initial[0]), height / (initial[3] - initial[2]))
    best_angle, best_scale, best_bounds = 0.0, initial_scale, initial
    for value in candidates:
        angle = (value + math.pi / 2) % math.pi - math.pi / 2
        bounds = _extent(hull, angle, reserve)
        scale = min(width / (bounds[1] - bounds[0]), height / (bounds[3] - bounds[2]))
        if scale > best_scale * (1 + 1e-12) or (
            abs(scale - best_scale) <= best_scale * 1e-12 and abs(angle) < abs(best_angle)
        ):
            best_angle, best_scale, best_bounds = angle, scale, bounds
    return best_angle, best_scale, best_bounds, initial_scale


def _resample(points: tuple[Point, ...], count: int = 1600) -> tuple[Point, ...]:
    """Sample equal polyline arc lengths for decorative tiles and the pixel grid."""
    closed = (*points, points[0])
    distances = [0.0]
    for a, b in zip(closed, closed[1:]):
        distances.append(distances[-1] + math.dist(a, b))
    sampled = []
    segment = 0
    for i in range(count):
        target = distances[-1] * i / count
        while distances[segment + 1] <= target:
            segment += 1
        first, last = closed[segment : segment + 2]
        fraction = (target - distances[segment]) / (distances[segment + 1] - distances[segment])
        sampled.append(
            (first[0] + (last[0] - first[0]) * fraction, first[1] + (last[1] - first[1]) * fraction)
        )
    return tuple(sampled)


@lru_cache(maxsize=128)
def fit_track(
    circuit_id: str, source: TrackSource, style: TrackStyle, width: int = 494, height: int = 271
) -> FittedTrack:
    """Project, rotate and bake coordinates into native pixels with complete ink margins."""
    if width < 64 or height < 64 or width > 800 or height > 480:
        raise ValueError("Track slot must be between 64x64 and 800x480 pixels")
    geometry = track_source(circuit_id, source)
    if geometry is None:
        raise ValueError("No reviewed circuit outline")
    raw, reserve = source_points(circuit_id, source)
    cs = math.sqrt(3) / 2
    projection: Matrix = (cs, 0.5, -cs, 0.5, 0, 0) if style == "relief" else (1, 0, 0, 1, 0, 0)
    points = tuple(transform(p, projection) for p in raw)
    reserve *= math.sqrt(1.5) if style == "relief" else 1
    left, top, right, bottom = INK_MARGINS[style]
    angle, scale, bounds, initial = best_rotation(
        points, reserve, width - left - right, height - top - bottom
    )
    xlo, xhi, ylo, yhi = bounds[:4]
    span_x, span_y = (xhi - xlo) * scale, (yhi - ylo) * scale
    fitted_width = min(width, math.ceil(span_x + left + right))
    fitted_height = min(height, math.ceil(span_y + top + bottom))
    tx = left + (fitted_width - left - right - span_x) / 2 - xlo * scale
    ty = top + (fitted_height - top - bottom - span_y) / 2 - ylo * scale
    c, s = math.cos(angle), math.sin(angle)
    rotation: Matrix = (c * scale, s * scale, -s * scale, c * scale, tx, ty)
    original = tuple(geometry["transform"])
    origin = transform(transform(transform((0, 0), original), projection), rotation)
    xunit = transform(transform(transform((1, 0), original), projection), rotation)
    yunit = transform(transform(transform((0, 1), original), projection), rotation)
    matrix: Matrix = (
        xunit[0] - origin[0],
        xunit[1] - origin[1],
        yunit[0] - origin[0],
        yunit[1] - origin[1],
        *origin,
    )
    commands = []
    for command in geometry["commands"]:
        coordinates = [
            value
            for i in range(1, len(command), 2)
            for value in transform((command[i], command[i + 1]), matrix)
        ]
        commands.append(command[0] + " ".join(f"{value:.4f}" for value in coordinates))
    return FittedTrack(
        fitted_width,
        fitted_height,
        " ".join(commands),
        tuple(transform(p, rotation) for p in _resample(points)),
        matrix,
        math.degrees(angle),
        scale,
        scale / initial,
        reserve * scale,
    )
