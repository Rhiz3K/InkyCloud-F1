#!/usr/bin/env python3
"""Parallel layered-search tuner for 1-bit track conversion.

Stage 1 runs a broad multi-core search using local preview metrics.
Stage 2 verifies the best finalists against the live `/calendar.bmp` endpoint.
"""

from __future__ import annotations

import argparse
import heapq
import io
import json
import os
import random
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytz
from PIL import Image, ImageFilter

from track_conversion_utils import (
    MAX_TRACK_HEIGHT,
    MAX_TRACK_WIDTH,
    SemanticScoringMetrics,
    Track1BitParams,
    TrackSemanticReference,
    build_1bit_track,
    build_track_semantic_reference,
    evaluate_rendered_semantic_quality,
    fetch_calendar_render,
    semantic_metrics_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PREVIEW_WIDTH = 500
PREVIEW_HEIGHT = 268
NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))

_SOURCE_DATA: dict[str, Any] | None = None
_SEMANTIC_REFERENCE: TrackSemanticReference | None = None


@dataclass(frozen=True)
class LayeredParams:
    road_seed_gray: int
    road_seed_sat: int
    road_grow_gray: int
    road_grow_sat: int
    road_dilate_px: int
    colored_sat: int
    colored_val: int
    road_proximity_px: int
    label_min_area: int
    label_min_width: int
    label_min_height: int
    label_min_fill: float
    label_max_aspect: float
    accent_max_area: int
    accent_min_aspect: float
    annotation_min_area: int
    annotation_max_area: int
    annotation_max_aspect: float
    annotation_proximity_px: int
    label_text_value: int
    label_text_low_sat: int
    label_text_low_sat_value: int
    min_component_pixels: int
    opaque_alpha: int


@dataclass(frozen=True)
class Component:
    points: list[tuple[int, int]]
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    area: int
    width: int
    height: int
    fill_ratio: float


def _pixel_to_int(pixel: int | float | tuple[int, ...]) -> int:
    if isinstance(pixel, tuple):
        return int(pixel[0]) if pixel else 0
    return int(pixel)


def _pixel_to_hsv(pixel: int | float | tuple[int, ...]) -> tuple[int, int, int]:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multi-core layered search for 1-bit track conversion."
    )
    parser.add_argument(
        "--source-path",
        type=Path,
        default=PROJECT_ROOT / "app" / "assets" / "tracks" / "albert_park.png",
        help="Source track image path",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=PROJECT_ROOT / "app" / "assets" / "tracks_processed" / "albert_park.bmp",
        help="Destination 1-bit BMP path",
    )
    parser.add_argument(
        "--endpoint-url",
        default="http://127.0.0.1:8000/calendar.bmp?lang=en&year=2026&round=1&display=1bit&weather=false",
        help="Endpoint URL used for finalist verification",
    )
    parser.add_argument("--trials", type=int, default=4000, help="Number of random candidates")
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help="Parallel worker count (default: CPU count)",
    )
    parser.add_argument(
        "--finalists",
        type=int,
        default=128,
        help="How many top local candidates to verify via endpoint",
    )
    parser.add_argument("--seed", type=int, default=20260306, help="Random seed")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=200,
        help="Progress interval for local search",
    )
    parser.add_argument(
        "--best-bmp-out",
        type=Path,
        default=Path("/tmp/albert_park_best_layered_parallel.bmp"),
        help="Snapshot path for best BMP",
    )
    parser.add_argument(
        "--best-render-out",
        type=Path,
        default=Path("/tmp/albert_park_best_layered_parallel.png"),
        help="Snapshot path for best rendered preview",
    )
    parser.add_argument(
        "--best-params-out",
        type=Path,
        default=Path("/tmp/albert_park_best_layered_parallel.json"),
        help="Path for saving best parameter set as JSON",
    )
    parser.add_argument(
        "--base-params-file",
        type=Path,
        help="Optional JSON file with base params for local fine-tuning",
    )
    parser.add_argument(
        "--local-scale",
        type=float,
        default=1.0,
        help="Fine-tuning jitter scale when --base-params-file is used",
    )
    return parser.parse_args()


def _connected_components(mask: list[list[bool]]) -> list[Component]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    visited = [[False] * width for _ in range(height)]
    components: list[Component] = []

    for y in range(height):
        for x in range(width):
            if not mask[y][x] or visited[y][x]:
                continue

            queue = [(x, y)]
            visited[y][x] = True
            points: list[tuple[int, int]] = []
            min_x = min_y = 10**9
            max_x = max_y = -1

            while queue:
                cx, cy = queue.pop()
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
                    fill_ratio=area / (comp_width * comp_height),
                )
            )

    return components


def _dilate_mask(mask: list[list[bool]], iterations: int) -> list[list[bool]]:
    if iterations <= 0:
        return mask

    height = len(mask)
    width = len(mask[0]) if height else 0
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Could not access dilation pixels")

    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                pixels[x, y] = 255

    for _ in range(iterations):
        image = image.filter(ImageFilter.MaxFilter(size=3))

    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Could not access dilated pixels")

    return [[_pixel_to_int(pixels[x, y]) > 0 for x in range(width)] for y in range(height)]


def _prepare_source_data(source_path: Path) -> dict[str, Any]:
    image = Image.open(source_path).convert("RGBA")
    alpha_image = image.getchannel("A")
    bbox = alpha_image.getbbox()
    if bbox:
        image = image.crop(bbox)
        alpha_image = alpha_image.crop(bbox)

    width, height = image.size
    ratio = min(MAX_TRACK_WIDTH / width, MAX_TRACK_HEIGHT / height)
    if ratio < 1:
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        alpha_image = alpha_image.resize(new_size, Image.Resampling.LANCZOS)

    composited = Image.new("RGB", image.size, (255, 255, 255))
    composited.paste(image, mask=alpha_image)

    alpha_pixels = alpha_image.load()
    gray_pixels = composited.convert("L").load()
    hsv_pixels = composited.convert("HSV").load()
    if alpha_pixels is None or gray_pixels is None or hsv_pixels is None:
        raise RuntimeError("Could not access source pixels")

    final_width, final_height = composited.size
    alpha = [
        [_pixel_to_int(alpha_pixels[x, y]) for x in range(final_width)] for y in range(final_height)
    ]
    gray = [
        [_pixel_to_int(gray_pixels[x, y]) for x in range(final_width)] for y in range(final_height)
    ]
    hsv = [
        [_pixel_to_hsv(hsv_pixels[x, y]) for x in range(final_width)] for y in range(final_height)
    ]

    return {
        "width": final_width,
        "height": final_height,
        "alpha": alpha,
        "gray": gray,
        "hsv": hsv,
    }


def _init_worker(source_path_str: str) -> None:
    global _SEMANTIC_REFERENCE, _SOURCE_DATA
    _SOURCE_DATA = _prepare_source_data(Path(source_path_str))
    _SEMANTIC_REFERENCE = build_track_semantic_reference(
        Path(source_path_str),
        preview_size=(PREVIEW_WIDTH, PREVIEW_HEIGHT),
    )


def _sample_params(rng: random.Random) -> LayeredParams:
    return LayeredParams(
        road_seed_gray=rng.randint(88, 126),
        road_seed_sat=rng.randint(120, 255),
        road_grow_gray=rng.randint(115, 180),
        road_grow_sat=rng.randint(120, 255),
        road_dilate_px=rng.choice([0, 0, 1]),
        colored_sat=rng.randint(56, 96),
        colored_val=rng.randint(70, 110),
        road_proximity_px=rng.randint(1, 3),
        label_min_area=rng.randint(120, 320),
        label_min_width=rng.randint(12, 24),
        label_min_height=rng.randint(6, 12),
        label_min_fill=rng.uniform(0.50, 0.90),
        label_max_aspect=rng.uniform(4.0, 10.0),
        accent_max_area=rng.randint(80, 2200),
        accent_min_aspect=rng.uniform(2.0, 14.0),
        annotation_min_area=rng.randint(10, 40),
        annotation_max_area=rng.randint(60, 260),
        annotation_max_aspect=rng.uniform(1.6, 4.5),
        annotation_proximity_px=rng.randint(1, 5),
        label_text_value=rng.randint(100, 150),
        label_text_low_sat=rng.randint(55, 110),
        label_text_low_sat_value=rng.randint(130, 185),
        min_component_pixels=rng.randint(4, 12),
        opaque_alpha=rng.randint(28, 50),
    )


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _jitter_int(rng: random.Random, base: int, spread: int, low: int, high: int) -> int:
    return _clamp_int(base + rng.randint(-spread, spread), low, high)


def _jitter_float(rng: random.Random, base: float, spread: float, low: float, high: float) -> float:
    return _clamp_float(base + rng.uniform(-spread, spread), low, high)


def _load_params_json(path: Path) -> LayeredParams:
    payload = json.loads(path.read_text())
    return LayeredParams(**payload)


def _sample_local_params(
    rng: random.Random, base: LayeredParams, local_scale: float
) -> LayeredParams:
    scale = max(0.1, local_scale)
    return LayeredParams(
        road_seed_gray=_jitter_int(rng, base.road_seed_gray, int(round(6 * scale)), 70, 180),
        road_seed_sat=_jitter_int(rng, base.road_seed_sat, int(round(28 * scale)), 0, 255),
        road_grow_gray=_jitter_int(rng, base.road_grow_gray, int(round(12 * scale)), 80, 255),
        road_grow_sat=_jitter_int(rng, base.road_grow_sat, int(round(28 * scale)), 0, 255),
        road_dilate_px=_clamp_int(
            base.road_dilate_px + rng.choice([-1, 0, 0, 1]),
            0,
            2,
        ),
        colored_sat=_jitter_int(rng, base.colored_sat, int(round(10 * scale)), 30, 140),
        colored_val=_jitter_int(rng, base.colored_val, int(round(10 * scale)), 40, 180),
        road_proximity_px=_jitter_int(rng, base.road_proximity_px, 1, 1, 5),
        label_min_area=_jitter_int(rng, base.label_min_area, int(round(50 * scale)), 40, 500),
        label_min_width=_jitter_int(rng, base.label_min_width, int(round(4 * scale)), 8, 40),
        label_min_height=_jitter_int(rng, base.label_min_height, int(round(2 * scale)), 4, 20),
        label_min_fill=_jitter_float(rng, base.label_min_fill, 0.08 * scale, 0.2, 0.95),
        label_max_aspect=_jitter_float(rng, base.label_max_aspect, 1.0 * scale, 2.0, 14.0),
        accent_max_area=_jitter_int(rng, base.accent_max_area, int(round(260 * scale)), 40, 3000),
        accent_min_aspect=_jitter_float(rng, base.accent_min_aspect, 0.8 * scale, 1.0, 16.0),
        annotation_min_area=_jitter_int(
            rng, base.annotation_min_area, int(round(6 * scale)), 1, 100
        ),
        annotation_max_area=_jitter_int(
            rng, base.annotation_max_area, int(round(40 * scale)), 20, 600
        ),
        annotation_max_aspect=_jitter_float(rng, base.annotation_max_aspect, 0.7 * scale, 1.0, 8.0),
        annotation_proximity_px=_jitter_int(rng, base.annotation_proximity_px, 1, 1, 6),
        label_text_value=_jitter_int(rng, base.label_text_value, int(round(10 * scale)), 60, 200),
        label_text_low_sat=_jitter_int(
            rng, base.label_text_low_sat, int(round(12 * scale)), 0, 255
        ),
        label_text_low_sat_value=_jitter_int(
            rng,
            base.label_text_low_sat_value,
            int(round(12 * scale)),
            80,
            220,
        ),
        min_component_pixels=_jitter_int(rng, base.min_component_pixels, 2, 1, 20),
        opaque_alpha=_jitter_int(rng, base.opaque_alpha, int(round(5 * scale)), 1, 100),
    )


def _build_layered_candidate(params: LayeredParams) -> tuple[bytes, SemanticScoringMetrics]:
    global _SEMANTIC_REFERENCE, _SOURCE_DATA
    if _SOURCE_DATA is None or _SEMANTIC_REFERENCE is None:
        raise RuntimeError("Worker source data not initialized")

    width = int(_SOURCE_DATA["width"])
    height = int(_SOURCE_DATA["height"])
    alpha = _SOURCE_DATA["alpha"]
    gray = _SOURCE_DATA["gray"]
    hsv = _SOURCE_DATA["hsv"]

    opaque = [[alpha[y][x] >= params.opaque_alpha for x in range(width)] for y in range(height)]

    road_seed = [
        [
            opaque[y][x]
            and gray[y][x] < params.road_seed_gray
            and hsv[y][x][1] < params.road_seed_sat
            for x in range(width)
        ]
        for y in range(height)
    ]

    road_mask = [[False] * width for _ in range(height)]
    seed_components = _connected_components(road_seed)
    if seed_components:
        largest_seed = max(seed_components, key=lambda comp: comp.area)
        queue = list(largest_seed.points)
        for x, y in largest_seed.points:
            road_mask[y][x] = True

        while queue:
            cx, cy = queue.pop()
            for dx, dy in NEIGHBORS:
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= width or ny >= height:
                    continue
                if road_mask[ny][nx] or not opaque[ny][nx]:
                    continue
                if gray[ny][nx] < params.road_grow_gray and hsv[ny][nx][1] < params.road_grow_sat:
                    road_mask[ny][nx] = True
                    queue.append((nx, ny))

    road_mask = _dilate_mask(road_mask, params.road_dilate_px)
    road_proximity = _dilate_mask(road_mask, params.road_proximity_px)
    annotation_proximity = _dilate_mask(road_mask, params.annotation_proximity_px)

    colored_mask = [
        [
            opaque[y][x]
            and hsv[y][x][1] >= params.colored_sat
            and hsv[y][x][2] >= params.colored_val
            for x in range(width)
        ]
        for y in range(height)
    ]

    label_mask = [[False] * width for _ in range(height)]
    accent_mask = [[False] * width for _ in range(height)]

    for component in _connected_components(colored_mask):
        aspect_ratio = max(component.width / component.height, component.height / component.width)
        touches_road = any(road_proximity[y][x] for x, y in component.points)

        if (
            not touches_road
            and component.area >= params.label_min_area
            and component.width >= params.label_min_width
            and component.height >= params.label_min_height
            and component.fill_ratio >= params.label_min_fill
            and aspect_ratio <= params.label_max_aspect
        ):
            for x, y in component.points:
                label_mask[y][x] = True
            continue

        if (
            touches_road
            and component.area <= params.accent_max_area
            and aspect_ratio >= params.accent_min_aspect
        ):
            for x, y in component.points:
                accent_mask[y][x] = True

    dark_small = [
        [
            opaque[y][x]
            and gray[y][x] < params.road_grow_gray
            and hsv[y][x][1] < params.road_grow_sat
            and not road_mask[y][x]
            for x in range(width)
        ]
        for y in range(height)
    ]

    annotation_mask = [[False] * width for _ in range(height)]
    for component in _connected_components(dark_small):
        aspect_ratio = max(component.width / component.height, component.height / component.width)
        near_road = any(annotation_proximity[y][x] for x, y in component.points)
        if (
            near_road
            and component.area >= params.annotation_min_area
            and component.area <= params.annotation_max_area
            and aspect_ratio <= params.annotation_max_aspect
            and component.fill_ratio <= 0.92
        ):
            for x, y in component.points:
                annotation_mask[y][x] = True

    black_mask = [
        [
            (road_mask[y][x] and not accent_mask[y][x]) or label_mask[y][x] or annotation_mask[y][x]
            for x in range(width)
        ]
        for y in range(height)
    ]

    for y in range(height):
        for x in range(width):
            if not label_mask[y][x]:
                continue
            sat = hsv[y][x][1]
            val = hsv[y][x][2]
            if val < params.label_text_value or (
                sat < params.label_text_low_sat and val < params.label_text_low_sat_value
            ):
                black_mask[y][x] = False

    for component in _connected_components(black_mask):
        if component.area < params.min_component_pixels:
            for x, y in component.points:
                black_mask[y][x] = False

    image = Image.new("1", (width, height), 1)
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("Could not access candidate pixels")
    for y in range(height):
        for x in range(width):
            if black_mask[y][x]:
                pixels[x, y] = 0

    preview = Image.new("1", (PREVIEW_WIDTH, PREVIEW_HEIGHT), 1)
    preview_x = (PREVIEW_WIDTH - width) // 2
    preview_y = (PREVIEW_HEIGHT - height) // 2
    preview.paste(image, (preview_x, preview_y))
    preview_metrics = evaluate_rendered_semantic_quality(
        preview,
        _SEMANTIC_REFERENCE,
        roi=(0, PREVIEW_WIDTH, 0, PREVIEW_HEIGHT),
    )

    buffer = io.BytesIO()
    image.save(buffer, format="BMP")
    bmp_bytes = buffer.getvalue()
    return bmp_bytes, preview_metrics


def _evaluate_candidate_preview_from_bytes(bmp_bytes: bytes) -> SemanticScoringMetrics:
    global _SEMANTIC_REFERENCE
    if _SEMANTIC_REFERENCE is None:
        raise RuntimeError("Worker semantic reference not initialized")
    image = Image.open(io.BytesIO(bmp_bytes)).convert("1")
    width, height = image.size
    preview = Image.new("1", (PREVIEW_WIDTH, PREVIEW_HEIGHT), 1)
    preview_x = (PREVIEW_WIDTH - width) // 2
    preview_y = (PREVIEW_HEIGHT - height) // 2
    preview.paste(image, (preview_x, preview_y))
    return evaluate_rendered_semantic_quality(
        preview,
        _SEMANTIC_REFERENCE,
        roi=(0, PREVIEW_WIDTH, 0, PREVIEW_HEIGHT),
    )


def _build_baseline_candidate(source_path: Path) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as handle:
        temp_path = Path(handle.name)

    try:
        build_1bit_track(source_path, temp_path, params=Track1BitParams())
        bmp_bytes = temp_path.read_bytes()
    finally:
        temp_path.unlink(missing_ok=True)

    preview_metrics = _evaluate_candidate_preview_from_bytes(bmp_bytes)
    return {
        "index": -1,
        "params": "baseline-default",
        "preview_metrics": preview_metrics,
        "preview_score": preview_metrics.total_score,
        "bmp_bytes": bmp_bytes,
    }


def _worker_trial(payload: tuple[int, LayeredParams]) -> dict[str, Any]:
    index, params = payload
    bmp_bytes, preview_metrics = _build_layered_candidate(params)
    preview_score = preview_metrics.total_score
    return {
        "index": index,
        "params": params,
        "preview_metrics": preview_metrics,
        "preview_score": preview_score,
        "bmp_bytes": bmp_bytes,
    }


def _write_bmp(output_path: Path, bmp_bytes: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bmp_bytes)


def _verify_finalists(
    finalists: list[dict[str, Any]],
    output_path: Path,
    endpoint_url: str,
    best_bmp_out: Path,
    best_render_out: Path,
    seed: int,
    semantic_reference: TrackSemanticReference,
) -> dict[str, Any]:
    timezones = list(pytz.all_timezones)
    random.Random(seed ^ 0x5F3759DF).shuffle(timezones)
    best_result: dict[str, Any] | None = None

    for finalist_index, finalist in enumerate(finalists):
        _write_bmp(output_path, finalist["bmp_bytes"])
        timezone_name = timezones[finalist_index % len(timezones)]
        rendered = fetch_calendar_render(endpoint_url, timezone_name)
        render_metrics = evaluate_rendered_semantic_quality(rendered, semantic_reference)
        render_score = render_metrics.total_score

        if best_result is None or render_score > best_result["render_score"]:
            best_result = {
                **finalist,
                "render_metrics": render_metrics,
                "render_score": render_score,
            }
            best_bmp_out.parent.mkdir(parents=True, exist_ok=True)
            best_render_out.parent.mkdir(parents=True, exist_ok=True)
            _write_bmp(best_bmp_out, finalist["bmp_bytes"])
            rendered.save(best_render_out)

    if best_result is None:
        raise RuntimeError("No finalist could be verified")

    _write_bmp(output_path, best_result["bmp_bytes"])
    return best_result


def main() -> int:
    global _SEMANTIC_REFERENCE
    args = parse_args()
    if not args.source_path.exists():
        print(f"Source not found: {args.source_path}")
        return 1

    rng = random.Random(args.seed)
    base_params = _load_params_json(args.base_params_file) if args.base_params_file else None
    if base_params is None:
        payloads = [(index, _sample_params(rng)) for index in range(args.trials)]
    else:
        payloads = [
            (index, _sample_local_params(rng, base_params, args.local_scale))
            for index in range(args.trials)
        ]

    finalists_heap: list[tuple[float, int, dict[str, Any]]] = []
    best_preview_score = float("-inf")
    semantic_reference = build_track_semantic_reference(
        args.source_path,
        preview_size=(PREVIEW_WIDTH, PREVIEW_HEIGHT),
    )
    _SEMANTIC_REFERENCE = semantic_reference
    best_preview_metrics: SemanticScoringMetrics | None = None

    baseline_result = _build_baseline_candidate(args.source_path)
    heapq.heappush(finalists_heap, (baseline_result["preview_score"], -1, baseline_result))
    best_preview_score = float(baseline_result["preview_score"])
    best_preview_metrics = baseline_result["preview_metrics"]

    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(str(args.source_path),),
    ) as executor:
        for processed, result in enumerate(
            executor.map(_worker_trial, payloads, chunksize=8), start=1
        ):
            preview_score = float(result["preview_score"])
            if preview_score > best_preview_score:
                best_preview_score = preview_score
                best_preview_metrics = result["preview_metrics"]

            heap_item = (preview_score, int(result["index"]), result)
            if len(finalists_heap) < args.finalists:
                heapq.heappush(finalists_heap, heap_item)
            elif preview_score > finalists_heap[0][0]:
                heapq.heapreplace(finalists_heap, heap_item)

            if processed % max(1, args.progress_every) == 0:
                metrics = semantic_metrics_to_dict(result["preview_metrics"])
                best_metrics_dict = (
                    semantic_metrics_to_dict(best_preview_metrics)
                    if best_preview_metrics is not None
                    else {}
                )
                print(
                    f"{processed}/{args.trials} "
                    f"best_preview={best_preview_score:.2f} current={preview_score:.2f} "
                    f"current_metrics={metrics} best_metrics={best_metrics_dict}"
                )

    finalists = [item[2] for item in sorted(finalists_heap, reverse=True)]
    best_result = _verify_finalists(
        finalists,
        output_path=args.output_path,
        endpoint_url=args.endpoint_url,
        best_bmp_out=args.best_bmp_out,
        best_render_out=args.best_render_out,
        seed=args.seed,
        semantic_reference=semantic_reference,
    )

    params_display = (
        asdict(best_result["params"])
        if isinstance(best_result["params"], LayeredParams)
        else best_result["params"]
    )

    print("\nLayered parallel search complete")
    print(f"  Workers: {args.workers}")
    print(f"  Trials: {args.trials}")
    print(f"  Search mode: {'local' if base_params is not None else 'broad'}")
    print(f"  Finalists verified: {len(finalists)}")
    print(f"  Best preview score: {best_result['preview_score']:.2f}")
    print(f"  Best render score: {best_result['render_score']:.2f}")
    print(f"  Preview metrics: {semantic_metrics_to_dict(best_result['preview_metrics'])}")
    print(f"  Render metrics: {semantic_metrics_to_dict(best_result['render_metrics'])}")
    print(f"  Params: {params_display}")
    print(f"  Applied output: {args.output_path}")
    print(f"  Best BMP snapshot: {args.best_bmp_out}")
    print(f"  Best render preview: {args.best_render_out}")

    if isinstance(best_result["params"], LayeredParams):
        args.best_params_out.parent.mkdir(parents=True, exist_ok=True)
        args.best_params_out.write_text(json.dumps(asdict(best_result["params"]), indent=2) + "\n")
        print(f"  Best params JSON: {args.best_params_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
