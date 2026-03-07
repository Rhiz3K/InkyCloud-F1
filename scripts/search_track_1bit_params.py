#!/usr/bin/env python3
"""Random-search tuner for 1-bit track conversion parameters.

The script scores candidates using rendered `/calendar.bmp` output so the
optimization follows what users actually see, not just the raw BMP asset.
"""

from __future__ import annotations

import argparse
import random
import shutil
from dataclasses import asdict
from pathlib import Path

import pytz

from track_conversion_utils import (
    Track1BitParams,
    build_1bit_track,
    evaluate_rendered_track_region,
    fetch_calendar_render,
    metrics_to_dict,
    score_track_metrics,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search 1-bit conversion parameters against rendered output quality."
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
        help="Output 1-bit BMP path used by renderer",
    )
    parser.add_argument(
        "--endpoint-url",
        default="http://127.0.0.1:8000/calendar.bmp?lang=en&year=2026&round=1&display=1bit&weather=false",
        help="Calendar endpoint for rendered quality checks",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=300,
        help="Number of random trials",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260305,
        help="Random seed",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Progress print interval",
    )
    parser.add_argument(
        "--best-bmp-out",
        type=Path,
        default=Path("/tmp/albert_park_best_track.bmp"),
        help="Path for saving best candidate BMP snapshot",
    )
    parser.add_argument(
        "--best-render-out",
        type=Path,
        default=Path("/tmp/albert_park_best_render.png"),
        help="Path for saving best rendered preview PNG",
    )
    return parser.parse_args()


def sample_params(rng: random.Random) -> Track1BitParams:
    """Sample one parameter set around currently successful ranges."""
    return Track1BitParams(
        road_gray_threshold=rng.randint(108, 126),
        road_saturation_threshold=rng.randint(140, 255),
        colored_saturation_threshold=rng.randint(56, 80),
        colored_value_threshold=rng.randint(78, 100),
        label_min_area=rng.randint(180, 280),
        label_min_width=rng.randint(14, 24),
        label_min_height=rng.randint(7, 11),
        label_min_fill_ratio=rng.uniform(0.62, 0.82),
        label_max_aspect_ratio=rng.uniform(4.0, 6.8),
        label_text_value_threshold=rng.randint(105, 135),
        label_text_low_sat_threshold=rng.randint(65, 95),
        label_text_value_low_sat_threshold=rng.randint(138, 168),
        min_component_pixels=rng.randint(6, 10),
        opaque_alpha_threshold=rng.randint(30, 45),
        road_dilate_px=rng.choice([0, 0, 1]),
    )


def main() -> int:
    args = parse_args()
    if not args.source_path.exists():
        print(f"Source not found: {args.source_path}")
        return 1

    rng = random.Random(args.seed)
    timezones = list(pytz.all_timezones)
    rng.shuffle(timezones)

    best_score = float("-inf")
    best_params: Track1BitParams | None = None
    best_metrics = None

    for index in range(args.trials):
        params = sample_params(rng)
        build_1bit_track(args.source_path, args.output_path, params=params)

        timezone_name = timezones[index % len(timezones)]
        try:
            rendered = fetch_calendar_render(args.endpoint_url, timezone_name)
        except Exception as exc:
            print(f"{index + 1:03d}/{args.trials} FAIL fetch ({timezone_name}): {exc}")
            continue

        metrics = evaluate_rendered_track_region(rendered)
        score = score_track_metrics(metrics)

        if score > best_score:
            best_score = score
            best_params = params
            best_metrics = metrics
            args.best_render_out.parent.mkdir(parents=True, exist_ok=True)
            rendered.save(args.best_render_out)
            args.best_bmp_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.output_path, args.best_bmp_out)

        if (index + 1) % max(1, args.progress_every) == 0:
            print(
                f"{index + 1:03d}/{args.trials} "
                f"best={best_score:.2f} current={score:.2f} "
                f"metrics={metrics_to_dict(metrics)}"
            )

    if best_params is None or best_metrics is None:
        print("No successful trial produced a scored candidate.")
        return 2

    build_1bit_track(args.source_path, args.output_path, params=best_params)

    print("\nBest candidate selected")
    print(f"  Score: {best_score:.2f}")
    print(f"  Metrics: {metrics_to_dict(best_metrics)}")
    print(f"  Params: {asdict(best_params)}")
    print(f"  Applied output: {args.output_path}")
    print(f"  Best BMP snapshot: {args.best_bmp_out}")
    print(f"  Best render preview: {args.best_render_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
