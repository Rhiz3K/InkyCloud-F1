#!/usr/bin/env python3
"""Utilities for track conversion and rendered quality scoring.

This module is intentionally script-friendly (no app imports) so it can be
used during rapid track asset iteration.
"""

from __future__ import annotations

import io
import math
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

MAX_TRACK_WIDTH = 490
MAX_TRACK_HEIGHT = 280
SPECTRA6_TARGET_SIZE = (494, 271)

NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))

SPECTRA6_PALETTE = (
    (0, 0, 0),
    (255, 255, 255),
    (160, 32, 32),
    (240, 224, 80),
    (80, 128, 184),
    (96, 128, 80),
)


@dataclass(frozen=True)
class Track1BitParams:
    """Parameter bundle for color-aware 1-bit track conversion."""

    road_gray_threshold: int = 118
    road_saturation_threshold: int = 255
    colored_saturation_threshold: int = 62
    colored_value_threshold: int = 85
    label_min_area: int = 220
    label_min_width: int = 20
    label_min_height: int = 9
    label_min_fill_ratio: float = 0.72
    label_max_aspect_ratio: float = 5.0
    label_text_value_threshold: int = 120
    label_text_low_sat_threshold: int = 80
    label_text_value_low_sat_threshold: int = 150
    min_component_pixels: int = 8
    opaque_alpha_threshold: int = 35
    road_dilate_px: int = 0


@dataclass(frozen=True)
class Component:
    """Connected component stats for a binary mask."""

    points: list[tuple[int, int]]
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    area: int
    width: int
    height: int
    fill_ratio: float


@dataclass(frozen=True)
class TrackRenderMetrics:
    """Quality metrics extracted from the rendered calendar region."""

    black_ratio: float
    largest_area: int
    largest_fill_ratio: float
    box_count: int
    box_white_ratio: float
    noise_count: int


@dataclass(frozen=True)
class SemanticScoringMetrics:
    """Semantic scoring metrics evaluated on the rendered track region."""

    track_black_fill_1x: float
    box_black_fill_1x: float
    text_white_fill_1x: float
    bg_white_fill_1x: float
    accent_white_fill_1x: float
    semantic_transfer_1x: float
    semantic_transfer_05x: float
    semantic_transfer_ms: float
    boundary_iou_track_1x: float
    boundary_iou_box_1x: float
    boundary_iou_track_05x: float
    boundary_iou_box_05x: float
    boundary_iou_ms: float
    hierarchy_score: float
    noise_score: float
    total_score: float


@dataclass(frozen=True)
class TrackSemanticReference:
    """Semantic reference masks for rendered-track scoring."""

    preview_size: tuple[int, int]
    masks_1x: dict[str, list[list[bool]]]
    masks_05x: dict[str, list[list[bool]]]
    boundaries_1x: dict[str, list[list[bool]]]
    boundaries_05x: dict[str, list[list[bool]]]


def download_file(url: str, output_path: Path, timeout: int = 30) -> int:
    """Download a file and return downloaded byte count."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "track-conversion/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    output_path.write_bytes(payload)
    return len(payload)


def _pixel_to_int(pixel: int | float | tuple[int, ...]) -> int:
    """Normalize Pillow pixel value to integer."""
    if isinstance(pixel, tuple):
        return int(pixel[0]) if pixel else 0
    return int(pixel)


def _pixel_to_hsv(pixel: int | float | tuple[int, ...]) -> tuple[int, int, int]:
    """Normalize Pillow HSV pixel value to (h, s, v) ints."""
    if isinstance(pixel, tuple):
        if len(pixel) >= 3:
            return int(pixel[0]), int(pixel[1]), int(pixel[2])
        if len(pixel) == 2:
            return int(pixel[0]), int(pixel[1]), int(pixel[1])
        if len(pixel) == 1:
            value = int(pixel[0])
            return value, 0, value
        return 0, 0, 0
    value = int(pixel)
    return value, 0, value


def _mask_shape(mask: list[list[bool]]) -> tuple[int, int]:
    """Return (width, height) for a bool mask."""
    height = len(mask)
    width = len(mask[0]) if height else 0
    return width, height


def _empty_mask(width: int, height: int) -> list[list[bool]]:
    """Create an empty bool mask."""
    return [[False] * width for _ in range(height)]


def _copy_mask(mask: list[list[bool]]) -> list[list[bool]]:
    """Deep-copy a bool mask."""
    return [row[:] for row in mask]


def _or_masks(*masks: list[list[bool]]) -> list[list[bool]]:
    """Return logical OR over masks of equal size."""
    if not masks:
        return []
    width, height = _mask_shape(masks[0])
    merged = _empty_mask(width, height)
    for y in range(height):
        for x in range(width):
            merged[y][x] = any(mask[y][x] for mask in masks)
    return merged


def _subtract_mask(mask: list[list[bool]], minus: list[list[bool]]) -> list[list[bool]]:
    """Return mask - minus."""
    width, height = _mask_shape(mask)
    result = _empty_mask(width, height)
    for y in range(height):
        for x in range(width):
            result[y][x] = mask[y][x] and not minus[y][x]
    return result


def _invert_mask(mask: list[list[bool]]) -> list[list[bool]]:
    """Invert a bool mask."""
    width, height = _mask_shape(mask)
    return [[not mask[y][x] for x in range(width)] for y in range(height)]


def _mask_to_image(mask: list[list[bool]]) -> Image.Image:
    """Convert bool mask to 1-bit Pillow image."""
    width, height = _mask_shape(mask)
    image = Image.new("1", (width, height), 0)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Failed to create mask image")
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                pixels[x, y] = 1
    return image


def _image_to_mask(image: Image.Image) -> list[list[bool]]:
    """Convert Pillow image to bool mask using nonzero as True."""
    binary = image.convert("1")
    pixels = binary.load()
    if pixels is None:
        raise RuntimeError("Failed to read image mask pixels")
    width, height = binary.size
    return [[_pixel_to_int(pixels[x, y]) > 0 for x in range(width)] for y in range(height)]


def _resize_mask(mask: list[list[bool]], size: tuple[int, int]) -> list[list[bool]]:
    """Resize bool mask with box filter and threshold at 0.5."""
    image = _mask_to_image(mask).convert("L")
    resized = image.resize(size, Image.Resampling.BOX)
    pixels = resized.load()
    if pixels is None:
        raise RuntimeError("Failed to read resized mask pixels")
    width, height = resized.size
    return [[_pixel_to_int(pixels[x, y]) >= 128 for x in range(width)] for y in range(height)]


def _paste_centered_mask(mask: list[list[bool]], canvas_size: tuple[int, int]) -> list[list[bool]]:
    """Paste smaller mask centered into larger canvas."""
    source_width, source_height = _mask_shape(mask)
    canvas_width, canvas_height = canvas_size
    canvas = _empty_mask(canvas_width, canvas_height)
    offset_x = (canvas_width - source_width) // 2
    offset_y = (canvas_height - source_height) // 2
    for y in range(source_height):
        for x in range(source_width):
            if mask[y][x]:
                canvas[offset_y + y][offset_x + x] = True
    return canvas


def _mask_area(mask: list[list[bool]]) -> int:
    """Return number of True pixels in mask."""
    return sum(sum(1 for value in row if value) for row in mask)


def _mean_mask_value(mask: list[list[bool]], values: list[list[bool]]) -> float:
    """Return mean of bool values restricted to mask."""
    total = 0
    count = 0
    width, height = _mask_shape(mask)
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                count += 1
                if values[y][x]:
                    total += 1
    return total / count if count else float("nan")


def _dilate_mask_radius(mask: list[list[bool]], radius: int) -> list[list[bool]]:
    """Dilate mask by radius using repeated 3x3 max filters."""
    return _dilate_mask(mask, radius)


def _erode_mask(mask: list[list[bool]], radius: int) -> list[list[bool]]:
    """Erode a bool mask using repeated 3x3 min filters."""
    if radius <= 0:
        return _copy_mask(mask)

    width, height = _mask_shape(mask)
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Failed to create erosion image")
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                pixels[x, y] = 255

    for _ in range(radius):
        image = image.filter(ImageFilter.MinFilter(size=3))

    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Failed to read eroded mask pixels")
    return [[_pixel_to_int(pixels[x, y]) > 0 for x in range(width)] for y in range(height)]


def _boundary_mask(mask: list[list[bool]], radius: int = 1) -> list[list[bool]]:
    """Return thin boundary mask from region mask."""
    eroded = _erode_mask(mask, radius)
    width, height = _mask_shape(mask)
    boundary = _empty_mask(width, height)
    for y in range(height):
        for x in range(width):
            boundary[y][x] = mask[y][x] and not eroded[y][x]
    return boundary


def _intersection_over_union(mask_a: list[list[bool]], mask_b: list[list[bool]]) -> float:
    """Compute IoU for two bool masks."""
    width, height = _mask_shape(mask_a)
    intersection = 0
    union = 0
    for y in range(height):
        for x in range(width):
            a = mask_a[y][x]
            b = mask_b[y][x]
            if a and b:
                intersection += 1
            if a or b:
                union += 1
    return intersection / union if union else 1.0


def _boundary_iou(
    predicted_mask: list[list[bool]], reference_mask: list[list[bool]], radius: int
) -> float:
    """Compute boundary IoU between predicted and reference masks."""
    predicted_boundary = _boundary_mask(predicted_mask, radius=1)
    reference_boundary = _boundary_mask(reference_mask, radius=1)
    predicted_band = _dilate_mask_radius(predicted_boundary, radius)
    reference_band = _dilate_mask_radius(reference_boundary, radius)
    pred_match = _and_masks(predicted_boundary, reference_band)
    ref_match = _and_masks(reference_boundary, predicted_band)
    intersection = _or_masks(pred_match, ref_match)
    union = _or_masks(predicted_boundary, reference_boundary)
    return _mask_area(intersection) / max(1, _mask_area(union))


def _and_masks(mask_a: list[list[bool]], mask_b: list[list[bool]]) -> list[list[bool]]:
    """Return logical AND of two masks."""
    width, height = _mask_shape(mask_a)
    return [[mask_a[y][x] and mask_b[y][x] for x in range(width)] for y in range(height)]


def _connected_components(mask: list[list[bool]]) -> list[Component]:
    """Return 4-neighborhood connected components for a bool mask."""
    height = len(mask)
    width = len(mask[0]) if height else 0
    visited = [[False] * width for _ in range(height)]
    components: list[Component] = []

    for y in range(height):
        for x in range(width):
            if not mask[y][x] or visited[y][x]:
                continue

            queue = deque([(x, y)])
            visited[y][x] = True
            points: list[tuple[int, int]] = []
            min_x = min_y = 10**9
            max_x = max_y = -1

            while queue:
                cx, cy = queue.popleft()
                points.append((cx, cy))
                min_x = min(min_x, cx)
                min_y = min(min_y, cy)
                max_x = max(max_x, cx)
                max_y = max(max_y, cy)

                for dx, dy in NEIGHBORS:
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if visited[ny][nx] or not mask[ny][nx]:
                        continue
                    visited[ny][nx] = True
                    queue.append((nx, ny))

            comp_width = max_x - min_x + 1
            comp_height = max_y - min_y + 1
            area = len(points)
            fill_ratio = area / (comp_width * comp_height)
            components.append(
                Component(
                    points=points,
                    min_x=min_x,
                    min_y=min_y,
                    max_x=max_x,
                    max_y=max_y,
                    area=area,
                    width=comp_width,
                    height=comp_height,
                    fill_ratio=fill_ratio,
                )
            )

    return components


def _dilate_mask(mask: list[list[bool]], iterations: int) -> list[list[bool]]:
    """Dilate a bool mask with a 3x3 max filter."""
    if iterations <= 0:
        return mask

    height = len(mask)
    width = len(mask[0]) if height else 0
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Failed to create mask pixel access")
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                pixels[x, y] = 255

    for _ in range(iterations):
        image = image.filter(ImageFilter.MaxFilter(size=3))

    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Failed to read dilated mask pixel access")
    return [[_pixel_to_int(pixels[x, y]) > 0 for x in range(width)] for y in range(height)]


def _prepare_track_rgba(
    input_path: Path,
    max_width: int = MAX_TRACK_WIDTH,
    max_height: int = MAX_TRACK_HEIGHT,
) -> Image.Image:
    """Load source track image, crop transparent margins, and fit max size."""
    image = Image.open(input_path).convert("RGBA")
    alpha = image.getchannel("A")

    bbox = alpha.getbbox()
    if bbox:
        image = image.crop(bbox)

    width, height = image.size
    ratio = min(max_width / width, max_height / height)
    if ratio < 1:
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    return image


def build_1bit_track(
    input_path: Path,
    output_path: Path,
    params: Track1BitParams | None = None,
    max_width: int = MAX_TRACK_WIDTH,
    max_height: int = MAX_TRACK_HEIGHT,
) -> dict[str, object]:
    """Generate 1-bit BMP from source track image using color-aware rules."""
    settings = params or Track1BitParams()
    rgba = _prepare_track_rgba(input_path, max_width=max_width, max_height=max_height)

    width, height = rgba.size
    alpha_channel = rgba.getchannel("A")
    composited = Image.new("RGB", rgba.size, (255, 255, 255))
    composited.paste(rgba, mask=alpha_channel)

    alpha = alpha_channel.load()
    gray = composited.convert("L").load()
    hsv = composited.convert("HSV").load()
    if alpha is None or gray is None or hsv is None:
        raise RuntimeError("Failed to load pixel data for 1-bit conversion")

    opaque = [
        [_pixel_to_int(alpha[x, y]) >= settings.opaque_alpha_threshold for x in range(width)]
        for y in range(height)
    ]

    road_seed = [
        [
            opaque[y][x]
            and _pixel_to_int(gray[x, y]) < settings.road_gray_threshold
            and _pixel_to_hsv(hsv[x, y])[1] < settings.road_saturation_threshold
            for x in range(width)
        ]
        for y in range(height)
    ]

    road_mask = [[False] * width for _ in range(height)]
    road_components = _connected_components(road_seed)
    if road_components:
        largest = max(road_components, key=lambda comp: comp.area)
        for x, y in largest.points:
            road_mask[y][x] = True

    road_mask = _dilate_mask(road_mask, settings.road_dilate_px)

    colored_mask = [
        [
            opaque[y][x]
            and _pixel_to_hsv(hsv[x, y])[1] >= settings.colored_saturation_threshold
            and _pixel_to_hsv(hsv[x, y])[2] >= settings.colored_value_threshold
            for x in range(width)
        ]
        for y in range(height)
    ]

    label_mask = [[False] * width for _ in range(height)]
    for component in _connected_components(colored_mask):
        aspect_ratio = max(
            component.width / component.height,
            component.height / component.width,
        )
        if (
            component.area >= settings.label_min_area
            and component.width >= settings.label_min_width
            and component.height >= settings.label_min_height
            and component.fill_ratio >= settings.label_min_fill_ratio
            and aspect_ratio <= settings.label_max_aspect_ratio
        ):
            for x, y in component.points:
                label_mask[y][x] = True

    black_mask = [
        [road_mask[y][x] or label_mask[y][x] for x in range(width)] for y in range(height)
    ]

    for y in range(height):
        for x in range(width):
            if not label_mask[y][x]:
                continue
            sat = _pixel_to_hsv(hsv[x, y])[1]
            val = _pixel_to_hsv(hsv[x, y])[2]
            if val < settings.label_text_value_threshold or (
                sat < settings.label_text_low_sat_threshold
                and val < settings.label_text_value_low_sat_threshold
            ):
                black_mask[y][x] = False

    for component in _connected_components(black_mask):
        if component.area < settings.min_component_pixels:
            for x, y in component.points:
                black_mask[y][x] = False

    output = Image.new("L", (width, height), 255)
    pixels = output.load()
    if pixels is None:
        raise RuntimeError("Failed to create output pixel access")
    for y in range(height):
        for x in range(width):
            if black_mask[y][x]:
                pixels[x, y] = 0

    final = output.convert("1")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(output_path, format="BMP")

    histogram = final.convert("L").histogram()
    black_pixels = histogram[0]
    white_pixels = histogram[255]
    total_pixels = max(1, black_pixels + white_pixels)

    return {
        "output_size": output_path.stat().st_size,
        "final_dimensions": final.size,
        "black_ratio": black_pixels / total_pixels,
        "black_pixels": black_pixels,
        "white_pixels": white_pixels,
    }


def convert_spectra6_track(
    input_path: Path,
    output_path: Path,
    target_size: tuple[int, int] = SPECTRA6_TARGET_SIZE,
) -> dict[str, object]:
    """Generate Spectra6 indexed BMP from source track image."""
    original = Image.open(input_path)
    if original.mode in ("RGBA", "LA") or (
        original.mode == "P" and "transparency" in original.info
    ):
        rgba = original.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        rgb = base
    else:
        rgb = original.convert("RGB")

    contained = ImageOps.contain(rgb, target_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target_size, (255, 255, 255))
    offset = ((target_size[0] - contained.width) // 2, (target_size[1] - contained.height) // 2)
    canvas.paste(contained, offset)

    palette_flat: list[int] = []
    for color in SPECTRA6_PALETTE:
        palette_flat.extend(color)
    while len(palette_flat) < 768:
        palette_flat.extend([0, 0, 0])

    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(palette_flat)

    indexed = canvas.quantize(colors=6, palette=palette_image, dither=Image.Dither.NONE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indexed.save(output_path, format="BMP")

    used_colors = indexed.getcolors(maxcolors=256)
    return {
        "output_size": output_path.stat().st_size,
        "final_dimensions": indexed.size,
        "contained_dimensions": contained.size,
        "offset": offset,
        "colors_used": len(used_colors) if used_colors is not None else 256,
    }


def build_track_semantic_reference(
    input_path: Path,
    preview_size: tuple[int, int] = (500, 268),
    max_width: int = MAX_TRACK_WIDTH,
    max_height: int = MAX_TRACK_HEIGHT,
    opaque_alpha_threshold: int = 35,
) -> TrackSemanticReference:
    """Build semantic reference masks from the colorful source track diagram."""
    rgba = _prepare_track_rgba(input_path, max_width=max_width, max_height=max_height)
    alpha_channel = rgba.getchannel("A")
    composited = Image.new("RGB", rgba.size, (255, 255, 255))
    composited.paste(rgba, mask=alpha_channel)

    width, height = rgba.size
    alpha = alpha_channel.load()
    gray = composited.convert("L").load()
    hsv = composited.convert("HSV").load()
    if alpha is None or gray is None or hsv is None:
        raise RuntimeError("Failed to load pixel data for semantic reference")

    opaque = [
        [_pixel_to_int(alpha[x, y]) >= opaque_alpha_threshold for x in range(width)]
        for y in range(height)
    ]

    dark_neutral = [
        [
            opaque[y][x] and _pixel_to_int(gray[x, y]) < 138 and _pixel_to_hsv(hsv[x, y])[1] < 128
            for x in range(width)
        ]
        for y in range(height)
    ]

    track_body = _empty_mask(width, height)
    dark_components = _connected_components(dark_neutral)
    if dark_components:
        dominant_track = max(dark_components, key=lambda component: component.area)
        for x, y in dominant_track.points:
            track_body[y][x] = True

    track_proximity = _dilate_mask_radius(track_body, 3)

    saturated_colored = [
        [
            opaque[y][x] and _pixel_to_hsv(hsv[x, y])[1] >= 56 and _pixel_to_hsv(hsv[x, y])[2] >= 70
            for x in range(width)
        ]
        for y in range(height)
    ]

    label_boxes = _empty_mask(width, height)
    accent_mask = _empty_mask(width, height)
    decor_ignore = _empty_mask(width, height)

    for component in _connected_components(saturated_colored):
        aspect_ratio = max(
            component.width / component.height,
            component.height / component.width,
        )
        near_track = any(track_proximity[y][x] for x, y in component.points)

        if (
            component.area >= 140
            and component.width >= 12
            and component.height >= 6
            and component.fill_ratio >= 0.48
            and aspect_ratio <= 9.5
            and not (near_track and aspect_ratio >= 6.5 and component.area < 1200)
        ):
            for x, y in component.points:
                label_boxes[y][x] = True
            continue

        if near_track and component.area <= 2200 and aspect_ratio >= 2.0:
            for x, y in component.points:
                accent_mask[y][x] = True
            continue

        if component.area <= 220:
            for x, y in component.points:
                decor_ignore[y][x] = True

    track_black = _subtract_mask(track_body, accent_mask)

    text_white = _empty_mask(width, height)
    for y in range(height):
        for x in range(width):
            if not label_boxes[y][x]:
                continue
            sat = _pixel_to_hsv(hsv[x, y])[1]
            val = _pixel_to_hsv(hsv[x, y])[2]
            if val < 125 or (sat < 92 and val < 165):
                text_white[y][x] = True

    background_white = _invert_mask(_or_masks(track_black, label_boxes, decor_ignore))
    accent_white = _copy_mask(accent_mask)

    masks_track_space = {
        "track_black": track_black,
        "box_black": label_boxes,
        "text_white": text_white,
        "accent_white": accent_white,
        "bg_white": background_white,
        "decor_ignore": decor_ignore,
    }

    masks_1x = {
        key: _paste_centered_mask(mask, preview_size) for key, mask in masks_track_space.items()
    }
    masks_05x = {
        key: _resize_mask(mask, (preview_size[0] // 2, preview_size[1] // 2))
        for key, mask in masks_1x.items()
    }

    boundaries_1x = {
        "track_black": _boundary_mask(masks_1x["track_black"], radius=1),
        "box_black": _boundary_mask(masks_1x["box_black"], radius=1),
    }
    boundaries_05x = {
        "track_black": _boundary_mask(masks_05x["track_black"], radius=1),
        "box_black": _boundary_mask(masks_05x["box_black"], radius=1),
    }

    return TrackSemanticReference(
        preview_size=preview_size,
        masks_1x=masks_1x,
        masks_05x=masks_05x,
        boundaries_1x=boundaries_1x,
        boundaries_05x=boundaries_05x,
    )


def _crop_or_use_full(image: Image.Image, roi: tuple[int, int, int, int] | None) -> Image.Image:
    """Crop to ROI if image is larger than ROI, otherwise use full image."""
    if roi is None:
        return image
    x0, x1, y0, y1 = roi
    target_width = x1 - x0
    target_height = y1 - y0
    if image.size == (target_width, target_height):
        return image
    return image.crop((x0, y0, x1, y1))


def _mask_from_black_pixels(image: Image.Image) -> list[list[bool]]:
    """Return True where the image is black."""
    binary = image.convert("1")
    pixels = binary.load()
    if pixels is None:
        raise RuntimeError("Failed to load candidate pixels")
    width, height = binary.size
    return [[_pixel_to_int(pixels[x, y]) == 0 for x in range(width)] for y in range(height)]


def _semantic_transfer_score(
    black_mask: list[list[bool]],
    white_mask: list[list[bool]],
    semantic_masks: dict[str, list[list[bool]]],
) -> float:
    """Compute semantic layer transfer score for one scale."""
    weighted_terms: list[tuple[float, float]] = []

    def add(weight: float, value: float) -> None:
        if not math.isnan(value):
            weighted_terms.append((weight, value))

    add(0.30, _mean_mask_value(semantic_masks["track_black"], black_mask))
    add(0.18, _mean_mask_value(semantic_masks["box_black"], black_mask))
    add(0.18, _mean_mask_value(semantic_masks["text_white"], white_mask))
    add(0.14, _mean_mask_value(semantic_masks["bg_white"], white_mask))
    add(0.10, _mean_mask_value(semantic_masks["accent_white"], white_mask))

    white_union = _or_masks(
        semantic_masks["text_white"],
        semantic_masks["bg_white"],
        semantic_masks["accent_white"],
    )
    spill_black = _mean_mask_value(white_union, black_mask)
    add(0.10, 1.0 - spill_black if not math.isnan(spill_black) else float("nan"))

    total_weight = sum(weight for weight, _ in weighted_terms)
    total_score = sum(weight * value for weight, value in weighted_terms)
    return total_score / total_weight if total_weight else 0.0


def _hierarchy_score(
    black_mask: list[list[bool]],
    semantic_masks: dict[str, list[list[bool]]],
) -> float:
    """Compare black-mass distribution against expected visual hierarchy."""
    track_black = _and_masks(black_mask, semantic_masks["track_black"])
    box_black = _and_masks(black_mask, semantic_masks["box_black"])
    decor_black = _and_masks(black_mask, semantic_masks["decor_ignore"])
    main_expected = _or_masks(semantic_masks["track_black"], semantic_masks["box_black"])
    outside_black = _subtract_mask(black_mask, main_expected)

    predicted = [
        float(_mask_area(track_black)),
        float(_mask_area(box_black)),
        float(_mask_area(decor_black)),
        float(_mask_area(outside_black)),
    ]
    expected = [
        float(_mask_area(semantic_masks["track_black"])),
        float(_mask_area(semantic_masks["box_black"])),
        0.0,
        0.0,
    ]

    predicted_sum = sum(predicted)
    expected_sum = sum(expected)
    if predicted_sum <= 0 or expected_sum <= 0:
        return 0.0

    predicted = [value / predicted_sum for value in predicted]
    expected = [value / expected_sum for value in expected]
    l1_distance = sum(abs(pred - exp) for pred, exp in zip(predicted, expected, strict=False))
    return max(0.0, 1.0 - 0.5 * l1_distance)


def _noise_score(
    black_mask: list[list[bool]],
    semantic_masks: dict[str, list[list[bool]]],
    max_component_pixels: int = 8,
) -> float:
    """Penalize tiny black speckles outside allowed-black zones."""
    allowed_black = _dilate_mask_radius(
        _or_masks(semantic_masks["track_black"], semantic_masks["box_black"]),
        1,
    )
    outside_allowed = _subtract_mask(black_mask, allowed_black)
    noise_count = sum(
        1
        for component in _connected_components(outside_allowed)
        if component.area <= max_component_pixels
    )
    return math.exp(-(noise_count / 30.0))


def evaluate_rendered_semantic_quality(
    image: Image.Image,
    reference: TrackSemanticReference,
    roi: tuple[int, int, int, int] | None = (0, 500, 92, 360),
) -> SemanticScoringMetrics:
    """Score rendered output using semantic masks and contour fidelity."""
    cropped = _crop_or_use_full(image.convert("1"), roi)
    black_1x = _mask_from_black_pixels(cropped)
    width, height = cropped.size
    expected_size = reference.preview_size
    if (width, height) != expected_size:
        raise ValueError(f"Unexpected crop size {(width, height)}; expected {expected_size}")

    white_1x = _invert_mask(black_1x)

    track_black_fill_1x = _mean_mask_value(reference.masks_1x["track_black"], black_1x)
    box_black_fill_1x = _mean_mask_value(reference.masks_1x["box_black"], black_1x)
    text_white_fill_1x = _mean_mask_value(reference.masks_1x["text_white"], white_1x)
    bg_white_fill_1x = _mean_mask_value(reference.masks_1x["bg_white"], white_1x)
    accent_white_fill_1x = _mean_mask_value(reference.masks_1x["accent_white"], white_1x)

    downsampled = cropped.convert("L").resize(
        (expected_size[0] // 2, expected_size[1] // 2),
        Image.Resampling.BOX,
    )
    black_05x = _mask_from_black_pixels(downsampled.convert("1"))
    white_05x = _invert_mask(black_05x)

    semantic_transfer_1x = _semantic_transfer_score(black_1x, white_1x, reference.masks_1x)
    semantic_transfer_05x = _semantic_transfer_score(black_05x, white_05x, reference.masks_05x)
    semantic_transfer_ms = 0.7 * semantic_transfer_1x + 0.3 * semantic_transfer_05x

    predicted_track_1x = _and_masks(
        black_1x, _dilate_mask_radius(reference.masks_1x["track_black"], 4)
    )
    predicted_box_1x = _and_masks(black_1x, _dilate_mask_radius(reference.masks_1x["box_black"], 2))
    predicted_track_05x = _and_masks(
        black_05x, _dilate_mask_radius(reference.masks_05x["track_black"], 2)
    )
    predicted_box_05x = _and_masks(
        black_05x, _dilate_mask_radius(reference.masks_05x["box_black"], 1)
    )

    boundary_iou_track_1x = _boundary_iou(
        predicted_track_1x, reference.masks_1x["track_black"], radius=3
    )
    boundary_iou_box_1x = _boundary_iou(predicted_box_1x, reference.masks_1x["box_black"], radius=2)
    boundary_iou_track_05x = _boundary_iou(
        predicted_track_05x, reference.masks_05x["track_black"], radius=2
    )
    boundary_iou_box_05x = _boundary_iou(
        predicted_box_05x, reference.masks_05x["box_black"], radius=1
    )
    boundary_iou_ms = 0.7 * (0.75 * boundary_iou_track_1x + 0.25 * boundary_iou_box_1x) + 0.3 * (
        0.75 * boundary_iou_track_05x + 0.25 * boundary_iou_box_05x
    )

    hierarchy_score = _hierarchy_score(black_1x, reference.masks_1x)
    noise_score = _noise_score(black_1x, reference.masks_1x)

    base_score = (
        0.68 * semantic_transfer_ms
        + 0.18 * boundary_iou_ms
        + 0.08 * hierarchy_score
        + 0.06 * noise_score
    )

    hard_factor = 1.0
    if not math.isnan(track_black_fill_1x) and track_black_fill_1x < 0.78:
        hard_factor *= max(0.0, track_black_fill_1x / 0.78)
    if not math.isnan(box_black_fill_1x) and box_black_fill_1x < 0.72:
        hard_factor *= max(0.0, box_black_fill_1x / 0.72)
    if not math.isnan(bg_white_fill_1x) and bg_white_fill_1x < 0.88:
        hard_factor *= max(0.0, bg_white_fill_1x / 0.88)
    if not math.isnan(text_white_fill_1x) and text_white_fill_1x < 0.45:
        hard_factor *= max(0.0, text_white_fill_1x / 0.45)

    total_score = 100.0 * base_score * hard_factor

    return SemanticScoringMetrics(
        track_black_fill_1x=track_black_fill_1x,
        box_black_fill_1x=box_black_fill_1x,
        text_white_fill_1x=text_white_fill_1x,
        bg_white_fill_1x=bg_white_fill_1x,
        accent_white_fill_1x=accent_white_fill_1x,
        semantic_transfer_1x=semantic_transfer_1x,
        semantic_transfer_05x=semantic_transfer_05x,
        semantic_transfer_ms=semantic_transfer_ms,
        boundary_iou_track_1x=boundary_iou_track_1x,
        boundary_iou_box_1x=boundary_iou_box_1x,
        boundary_iou_track_05x=boundary_iou_track_05x,
        boundary_iou_box_05x=boundary_iou_box_05x,
        boundary_iou_ms=boundary_iou_ms,
        hierarchy_score=hierarchy_score,
        noise_score=noise_score,
        total_score=total_score,
    )


def fetch_calendar_render(endpoint_url: str, timezone_name: str, timeout: int = 20) -> Image.Image:
    """Fetch rendered calendar BMP from endpoint and return as 1-bit PIL image."""
    url_parts = urllib.parse.urlsplit(endpoint_url)
    query = urllib.parse.parse_qsl(url_parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "tz"]
    query.append(("tz", timezone_name))
    final_query = urllib.parse.urlencode(query)
    final_url = urllib.parse.urlunsplit(
        (url_parts.scheme, url_parts.netloc, url_parts.path, final_query, url_parts.fragment)
    )

    request = urllib.request.Request(final_url, headers={"User-Agent": "track-conversion/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return Image.open(io.BytesIO(payload)).convert("1")


def evaluate_rendered_track_region(
    image: Image.Image,
    roi: tuple[int, int, int, int] = (0, 500, 92, 360),
) -> TrackRenderMetrics:
    """Evaluate rendered map region and return summary metrics."""
    binary = image.convert("1")
    pixels = binary.load()
    if pixels is None:
        raise RuntimeError("Failed to load pixels from rendered image")
    image_width, image_height = binary.size

    x0 = max(0, min(image_width, roi[0]))
    x1 = max(x0 + 1, min(image_width, roi[1]))
    y0 = max(0, min(image_height, roi[2]))
    y1 = max(y0 + 1, min(image_height, roi[3]))

    width = x1 - x0
    height = y1 - y0
    mask = [[pixels[x0 + x, y0 + y] == 0 for x in range(width)] for y in range(height)]

    components = _connected_components(mask)
    if not components:
        return TrackRenderMetrics(0.0, 0, 0.0, 0, 0.0, 0)

    total_pixels = width * height
    black_pixels = sum(component.area for component in components)
    black_ratio = black_pixels / max(1, total_pixels)

    largest = max(components, key=lambda component: component.area)

    box_count = 0
    box_white_ratios: list[float] = []
    for component in components:
        aspect_ratio = max(
            component.width / component.height,
            component.height / component.width,
        )
        if (
            70 <= component.area <= 1000
            and component.width >= 10
            and component.height >= 5
            and component.fill_ratio >= 0.45
            and aspect_ratio <= 8
            and component.min_y > 120
        ):
            box_count += 1
            box_white = 0
            box_total = component.width * component.height
            for yy in range(component.min_y, component.max_y + 1):
                for xx in range(component.min_x, component.max_x + 1):
                    if not mask[yy][xx]:
                        box_white += 1
            box_white_ratios.append(box_white / max(1, box_total))

    noise_count = sum(1 for component in components if component.area < 6)
    box_white_ratio = sum(box_white_ratios) / len(box_white_ratios) if box_white_ratios else 0.0

    return TrackRenderMetrics(
        black_ratio=black_ratio,
        largest_area=largest.area,
        largest_fill_ratio=largest.fill_ratio,
        box_count=box_count,
        box_white_ratio=box_white_ratio,
        noise_count=noise_count,
    )


def score_track_metrics(metrics: TrackRenderMetrics) -> float:
    """Produce a scalar score for automated candidate ranking."""

    def bell(value: float, center: float, spread: float) -> float:
        return math.exp(-(((value - center) / spread) ** 2))

    score = (
        40 * bell(metrics.black_ratio, 0.11, 0.03)
        + 25 * bell(float(metrics.largest_area), 13200.0, 2500.0)
        + 10 * bell(metrics.largest_fill_ratio, 0.12, 0.035)
        + 20 * (min(metrics.box_count, 3) / 3)
        + 5 * bell(metrics.box_white_ratio, 0.16, 0.08)
        + 5 * bell(float(metrics.noise_count), 0.0, 12.0)
    )

    if metrics.box_count < 3:
        score -= (3 - metrics.box_count) * 10

    return score


def metrics_to_dict(metrics: TrackRenderMetrics) -> dict[str, float | int]:
    """Convert TrackRenderMetrics to serializable dictionary."""
    return {
        "black_ratio": round(metrics.black_ratio, 4),
        "largest_area": metrics.largest_area,
        "largest_fill_ratio": round(metrics.largest_fill_ratio, 4),
        "box_count": metrics.box_count,
        "box_white_ratio": round(metrics.box_white_ratio, 4),
        "noise_count": metrics.noise_count,
    }


def semantic_metrics_to_dict(metrics: SemanticScoringMetrics) -> dict[str, float]:
    """Convert SemanticScoringMetrics to a serializable dictionary."""
    return {
        "track_black_fill_1x": round(metrics.track_black_fill_1x, 4),
        "box_black_fill_1x": round(metrics.box_black_fill_1x, 4),
        "text_white_fill_1x": round(metrics.text_white_fill_1x, 4),
        "bg_white_fill_1x": round(metrics.bg_white_fill_1x, 4),
        "accent_white_fill_1x": round(metrics.accent_white_fill_1x, 4),
        "semantic_transfer_1x": round(metrics.semantic_transfer_1x, 4),
        "semantic_transfer_05x": round(metrics.semantic_transfer_05x, 4),
        "semantic_transfer_ms": round(metrics.semantic_transfer_ms, 4),
        "boundary_iou_track_1x": round(metrics.boundary_iou_track_1x, 4),
        "boundary_iou_box_1x": round(metrics.boundary_iou_box_1x, 4),
        "boundary_iou_track_05x": round(metrics.boundary_iou_track_05x, 4),
        "boundary_iou_box_05x": round(metrics.boundary_iou_box_05x, 4),
        "boundary_iou_ms": round(metrics.boundary_iou_ms, 4),
        "hierarchy_score": round(metrics.hierarchy_score, 4),
        "noise_score": round(metrics.noise_score, 4),
        "total_score": round(metrics.total_score, 2),
    }
