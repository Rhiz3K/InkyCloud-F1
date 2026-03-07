#!/usr/bin/env python3
"""Score rendered track quality from `/calendar.bmp` endpoint output."""

from __future__ import annotations

import argparse
from pathlib import Path

from track_conversion_utils import (
    build_track_semantic_reference,
    evaluate_rendered_semantic_quality,
    fetch_calendar_render,
    semantic_metrics_to_dict,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score rendered 1-bit track quality.")
    parser.add_argument(
        "--endpoint-url",
        default="http://127.0.0.1:8000/calendar.bmp?lang=en&year=2026&round=1&display=1bit&weather=false",
        help="Calendar endpoint URL",
    )
    parser.add_argument(
        "--tz",
        default="Europe/Prague",
        help="Timezone to request (forces cache-busting for endpoint render)",
    )
    parser.add_argument(
        "--save-preview",
        type=Path,
        default=Path("/tmp/albert_park_render_score_preview.png"),
        help="Optional path to save rendered preview PNG",
    )
    parser.add_argument(
        "--source-path",
        type=Path,
        default=Path("app/assets/tracks/albert_park.png"),
        help="Source colorful track image used to build semantic reference",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reference = build_track_semantic_reference(args.source_path)
    rendered = fetch_calendar_render(args.endpoint_url, args.tz)
    metrics = evaluate_rendered_semantic_quality(rendered, reference)

    args.save_preview.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(args.save_preview)

    print(f"Score: {metrics.total_score:.2f}")
    print(f"Metrics: {semantic_metrics_to_dict(metrics)}")
    print(f"Preview: {args.save_preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
