#!/usr/bin/env python3
"""
F1 E-Ink Calendar - Performance Benchmark

Comprehensive benchmark comparing different serving strategies:
1. In-memory cache (dict lookup)
2. Pre-generated file (disk read)
3. On-the-fly rendering (full PIL pipeline)
4. HTTP endpoint (FastAPI TestClient)

Usage:
    uv run python scripts/benchmark_renderer.py
    uv run python scripts/benchmark_renderer.py --http
    uv run python scripts/benchmark_renderer.py --json results.json
    uv run python scripts/benchmark_renderer.py --runs 50 -v
"""

import argparse
import gc
import json
import platform
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import (
    ConstructorInfo,
    DriverInfo,
    HistoricalData,
    QualifyingResultEntry,
    RaceResultEntry,
)
from app.services.i18n import get_translator
from app.services.renderer import Renderer

# Constants
DEFAULT_RUNS = 50
DEFAULT_WARMUP = 3
SUPPORTED_LANGUAGES = ["en", "cs"]


# =============================================================================
# Mock Data Generators
# =============================================================================


def get_mock_race_data() -> dict:
    """Get mock race data for benchmarking."""
    return {
        "race_name": "Australian Grand Prix",
        "season": 2025,
        "round": 1,
        "circuit": {
            "circuitId": "albert_park",
            "name": "Albert Park Circuit",
            "location": "Melbourne",
            "country": "Australia",
        },
        "schedule": [
            {"name": "Practice 1", "datetime": "2025-03-14T01:30:00+00:00"},
            {"name": "Practice 2", "datetime": "2025-03-14T05:00:00+00:00"},
            {"name": "Practice 3", "datetime": "2025-03-15T01:30:00+00:00"},
            {"name": "Qualifying", "datetime": "2025-03-15T05:00:00+00:00"},
            {"name": "Race", "datetime": "2025-03-16T04:00:00+00:00"},
        ],
    }


def get_mock_historical_data() -> HistoricalData:
    """Get mock historical data for benchmarking."""
    driver1 = DriverInfo(code="VER", given_name="Max", family_name="Verstappen")
    driver2 = DriverInfo(code="SAI", given_name="Carlos", family_name="Sainz")
    driver3 = DriverInfo(code="NOR", given_name="Lando", family_name="Norris")

    red_bull = ConstructorInfo(name="Red Bull Racing")
    ferrari = ConstructorInfo(name="Ferrari")
    mclaren = ConstructorInfo(name="McLaren")

    return HistoricalData(
        season=2024,
        is_new_track=False,
        qualifying_results=[
            QualifyingResultEntry(
                position=1, driver=driver1, constructor=red_bull, q3_time="1:15.915"
            ),
            QualifyingResultEntry(
                position=2, driver=driver2, constructor=ferrari, q3_time="1:16.272"
            ),
            QualifyingResultEntry(
                position=3, driver=driver3, constructor=mclaren, q3_time="1:16.340"
            ),
        ],
        race_results=[
            RaceResultEntry(position=1, driver=driver2, constructor=ferrari, time="1:20:26.843"),
            RaceResultEntry(position=2, driver=driver3, constructor=mclaren, time="+2.366"),
            RaceResultEntry(position=3, driver=driver1, constructor=red_bull, time="+3.799"),
        ],
    )


# =============================================================================
# Benchmark Functions
# =============================================================================


def benchmark_memory_cache(
    bmp_data: bytes, num_runs: int = DEFAULT_RUNS, warmup: int = DEFAULT_WARMUP
) -> dict:
    """
    Benchmark in-memory cache lookup (dict access).

    This simulates the _bmp_cache dict lookup in main.py.
    """
    cache: dict[str, bytes] = {}
    cache_key = "calendar_en_Europe/Prague"
    cache[cache_key] = bmp_data

    # Warmup
    for _ in range(warmup):
        _ = cache.get(cache_key)

    # Benchmark
    times = []
    for _ in range(num_runs):
        start = time.perf_counter()
        _ = cache.get(cache_key)
        times.append((time.perf_counter() - start) * 1000)

    return _calculate_stats(times, num_runs, len(bmp_data))


def benchmark_file_read(
    file_path: Path, num_runs: int = DEFAULT_RUNS, warmup: int = DEFAULT_WARMUP
) -> dict:
    """
    Benchmark reading pre-generated BMP from disk.

    This simulates serving FileResponse from IMAGES_PATH.
    """
    if not file_path.exists():
        return {"error": f"File not found: {file_path}"}

    # Warmup (also warms OS file cache)
    for _ in range(warmup):
        _ = file_path.read_bytes()

    # Benchmark
    times = []
    data = b""
    for _ in range(num_runs):
        start = time.perf_counter()
        data = file_path.read_bytes()
        times.append((time.perf_counter() - start) * 1000)

    return _calculate_stats(times, num_runs, len(data))


def benchmark_render(
    num_runs: int = DEFAULT_RUNS, warmup: int = DEFAULT_WARMUP, verbose: bool = False
) -> dict:
    """
    Benchmark full on-the-fly rendering pipeline.

    This measures the complete Renderer.render_calendar() execution.
    """
    race_data = get_mock_race_data()
    historical_data = get_mock_historical_data()
    translator = get_translator("en")

    # Measure renderer initialization separately
    init_times = []
    for _ in range(3):
        gc.collect()
        start = time.perf_counter()
        _ = Renderer(translator)
        init_times.append((time.perf_counter() - start) * 1000)

    renderer = Renderer(translator)

    # Warmup
    for _ in range(warmup):
        _ = renderer.render_calendar(race_data, historical_data)

    # Benchmark
    times = []
    bmp_data = b""
    for i in range(num_runs):
        gc.collect()
        start = time.perf_counter()
        bmp_data = renderer.render_calendar(race_data, historical_data)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        if verbose and (i + 1) % 10 == 0:
            print(f"  Render run {i + 1}/{num_runs}: {elapsed:.1f} ms")

    stats = _calculate_stats(times, num_runs, len(bmp_data))
    stats["init_ms"] = {
        "mean": statistics.mean(init_times),
        "min": min(init_times),
        "max": max(init_times),
    }
    return stats


def benchmark_http_endpoint(
    num_runs: int = DEFAULT_RUNS, warmup: int = DEFAULT_WARMUP, verbose: bool = False
) -> dict:
    """
    Benchmark HTTP endpoint using FastAPI TestClient.

    This measures real endpoint performance including HTTP stack overhead.
    """
    try:
        from fastapi.testclient import TestClient

        from app.main import app
    except ImportError as e:
        return {"error": f"Import error: {e}"}

    client = TestClient(app)

    # Warmup
    for _ in range(warmup):
        response = client.get("/calendar.bmp?lang=en")
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}"}

    # Benchmark
    times = []
    content_length = 0
    for i in range(num_runs):
        start = time.perf_counter()
        response = client.get("/calendar.bmp?lang=en")
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        content_length = len(response.content)
        if verbose and (i + 1) % 10 == 0:
            print(f"  HTTP run {i + 1}/{num_runs}: {elapsed:.1f} ms")

    client.close()
    return _calculate_stats(times, num_runs, content_length)


def _calculate_stats(times: list[float], num_runs: int, size_bytes: int) -> dict:
    """Calculate statistics from timing measurements."""
    mean_time = statistics.mean(times)
    return {
        "num_runs": num_runs,
        "size_bytes": size_bytes,
        "mean_ms": mean_time,
        "median_ms": statistics.median(times),
        "min_ms": min(times),
        "max_ms": max(times),
        "stdev_ms": statistics.stdev(times) if len(times) > 1 else 0,
        "theoretical_rps": 1000 / mean_time if mean_time > 0 else 0,
    }


# =============================================================================
# Memory Profiling
# =============================================================================


def measure_memory_usage(verbose: bool = False) -> dict:
    """
    Measure memory usage during rendering using tracemalloc.

    Returns memory statistics for:
    - Single render operation peak memory
    - BMP data size in memory
    - Renderer object size estimate
    """
    race_data = get_mock_race_data()
    historical_data = get_mock_historical_data()
    translator = get_translator("en")

    # Force garbage collection before measurement
    gc.collect()

    # Start memory tracking
    tracemalloc.start()

    # Measure renderer initialization
    snapshot_before = tracemalloc.take_snapshot()
    renderer = Renderer(translator)
    snapshot_after_init = tracemalloc.take_snapshot()

    # Measure render operation
    bmp_data = renderer.render_calendar(race_data, historical_data)
    snapshot_after_render = tracemalloc.take_snapshot()

    # Calculate differences
    init_diff = snapshot_after_init.compare_to(snapshot_before, "lineno")
    render_diff = snapshot_after_render.compare_to(snapshot_after_init, "lineno")

    # Get peak memory
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Sum up memory allocations
    init_memory = sum(stat.size_diff for stat in init_diff if stat.size_diff > 0)
    render_memory = sum(stat.size_diff for stat in render_diff if stat.size_diff > 0)

    result = {
        "bmp_size_bytes": len(bmp_data),
        "bmp_size_kb": len(bmp_data) / 1024,
        "renderer_init_bytes": init_memory,
        "renderer_init_kb": init_memory / 1024,
        "render_operation_bytes": render_memory,
        "render_operation_kb": render_memory / 1024,
        "peak_memory_bytes": peak,
        "peak_memory_kb": peak / 1024,
        "peak_memory_mb": peak / (1024 * 1024),
    }

    if verbose:
        print("\n  Top 5 memory allocations during render:")
        for stat in render_diff[:5]:
            print(f"    {stat.size_diff / 1024:.1f} KB: {stat.traceback.format()[0]}")

    return result


def measure_cache_memory(languages: list[str] | None = None) -> dict:
    """
    Measure memory used by caching all language variants.

    Simulates the _bmp_cache dict with all pre-rendered images.
    """
    if languages is None:
        languages = SUPPORTED_LANGUAGES.copy()
    race_data = get_mock_race_data()
    historical_data = get_mock_historical_data()

    gc.collect()
    tracemalloc.start()

    cache: dict[str, bytes] = {}
    for lang in languages:
        translator = get_translator(lang)
        renderer = Renderer(translator)
        bmp_data = renderer.render_calendar(race_data, historical_data)
        cache[f"calendar_{lang}"] = bmp_data

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_size = sum(len(v) for v in cache.values())

    return {
        "num_languages": len(languages),
        "languages": languages,
        "total_cache_bytes": total_size,
        "total_cache_kb": total_size / 1024,
        "per_image_kb": (total_size / len(languages)) / 1024,
        "peak_memory_kb": peak / 1024,
    }


# =============================================================================
# Output Formatting
# =============================================================================


def print_header():
    """Print benchmark header."""
    print()
    print("=" * 70)
    print(" F1 E-Ink Calendar - Performance Benchmark")
    print("=" * 70)
    print(f" Python:    {platform.python_version()}")
    print(f" Platform:  {platform.system()} {platform.machine()}")
    print(f" Time:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


def print_benchmark_result(name: str, result: dict, index: int):
    """Print single benchmark result."""
    print(f"\n {index}. {name}")
    print("-" * 70)

    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return

    print(f"    Runs:           {result['num_runs']}")
    print(
        f"    Data size:      {result['size_bytes']:,} bytes ({result['size_bytes'] / 1024:.1f} KB)"
    )
    print(f"    Mean:           {result['mean_ms']:.3f} ms")
    print(f"    Median:         {result['median_ms']:.3f} ms")
    print(f"    Min:            {result['min_ms']:.3f} ms")
    print(f"    Max:            {result['max_ms']:.3f} ms")
    print(f"    StdDev:         {result['stdev_ms']:.3f} ms")
    print(f"    Throughput:     {result['theoretical_rps']:,.0f} req/s (theoretical)")

    if "init_ms" in result:
        print(f"    Renderer init:  {result['init_ms']['mean']:.2f} ms")


def print_memory_results(memory: dict, cache_memory: dict):
    """Print memory profiling results."""
    print("\n" + "=" * 70)
    print(" MEMORY ANALYSIS")
    print("=" * 70)

    print("\n Single Render Operation:")
    print("-" * 70)
    print(f"    BMP output size:      {memory['bmp_size_kb']:.1f} KB")
    print(f"    Renderer init:        {memory['renderer_init_kb']:.1f} KB")
    print(f"    Render operation:     {memory['render_operation_kb']:.1f} KB")
    print(f"    Peak memory:          {memory['peak_memory_mb']:.2f} MB")

    langs = ", ".join(cache_memory["languages"])
    print(f"\n Cache Memory ({cache_memory['num_languages']} languages: {langs}):")
    print("-" * 70)
    print(f"    Total cache size:     {cache_memory['total_cache_kb']:.1f} KB")
    print(f"    Per image:            {cache_memory['per_image_kb']:.1f} KB")
    print(f"    Peak during gen:      {cache_memory['peak_memory_kb']:.1f} KB")


def print_comparison(results: dict):
    """Print comparison table."""
    print("\n" + "=" * 70)
    print(" COMPARISON")
    print("=" * 70)

    cache_ms = results.get("cache", {}).get("mean_ms", 0)
    file_ms = results.get("file", {}).get("mean_ms", 0)
    render_ms = results.get("render", {}).get("mean_ms", 0)
    http_ms = results.get("http", {}).get("mean_ms", 0)

    print("\n Speedup Factors:")
    print("-" * 70)

    if render_ms > 0 and file_ms > 0:
        speedup = render_ms / file_ms
        print(f"    File vs Render:       {speedup:,.0f}x faster")

    if render_ms > 0 and cache_ms > 0:
        speedup = render_ms / cache_ms
        print(f"    Cache vs Render:      {speedup:,.0f}x faster")

    if file_ms > 0 and cache_ms > 0:
        speedup = file_ms / cache_ms
        print(f"    Cache vs File:        {speedup:,.0f}x faster")

    if http_ms > 0 and render_ms > 0:
        overhead = http_ms - render_ms
        print(f"    HTTP overhead:        +{overhead:.1f} ms (vs pure render)")

    print("\n Time Comparison:")
    print("-" * 70)
    print(f"    {'Method':<25} {'Time':>12} {'Throughput':>15}")
    print(f"    {'-' * 25} {'-' * 12} {'-' * 15}")

    if cache_ms > 0:
        rps = results["cache"]["theoretical_rps"]
        print(f"    {'In-memory cache':<25} {cache_ms:>10.3f} ms {rps:>12,.0f}/s")
    if file_ms > 0:
        rps = results["file"]["theoretical_rps"]
        print(f"    {'File read':<25} {file_ms:>10.3f} ms {rps:>12,.0f}/s")
    if render_ms > 0:
        rps = results["render"]["theoretical_rps"]
        print(f"    {'On-the-fly render':<25} {render_ms:>10.1f} ms {rps:>12,.0f}/s")
    if http_ms > 0:
        rps = results["http"]["theoretical_rps"]
        print(f"    {'HTTP endpoint':<25} {http_ms:>10.1f} ms {rps:>12,.0f}/s")


def print_footer():
    """Print benchmark footer."""
    print("\n" + "=" * 70)
    print(" NOTES")
    print("=" * 70)
    print(" - In-memory cache: Best for high-traffic, RAM allows")
    print(" - Pre-generated files: Good balance, minimal RAM usage")
    print(" - On-the-fly: Flexible but CPU intensive")
    print(" - Current strategy: Hourly pre-generation + in-memory cache")
    print("=" * 70)
    print()


# =============================================================================
# JSON Export
# =============================================================================


def save_results_json(results: dict, memory: dict, cache_memory: dict, output_path: Path):
    """Save benchmark results to JSON file."""
    output: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine(),
        },
        "benchmarks": {
            "cache_lookup_ms": results.get("cache", {}).get("mean_ms"),
            "file_read_ms": results.get("file", {}).get("mean_ms"),
            "render_ms": results.get("render", {}).get("mean_ms"),
            "http_endpoint_ms": results.get("http", {}).get("mean_ms"),
        },
        "throughput_rps": {
            "cache": results.get("cache", {}).get("theoretical_rps"),
            "file": results.get("file", {}).get("theoretical_rps"),
            "render": results.get("render", {}).get("theoretical_rps"),
            "http": results.get("http", {}).get("theoretical_rps"),
        },
        "memory": {
            "bmp_size_bytes": memory.get("bmp_size_bytes"),
            "render_peak_kb": memory.get("peak_memory_kb"),
            "cache_total_kb": cache_memory.get("total_cache_kb"),
            "cache_per_image_kb": cache_memory.get("per_image_kb"),
        },
        "comparison": {},
        "raw_results": results,
    }

    # Calculate comparisons
    cache_ms = results.get("cache", {}).get("mean_ms", 0)
    file_ms = results.get("file", {}).get("mean_ms", 0)
    render_ms = results.get("render", {}).get("mean_ms", 0)

    if render_ms > 0 and file_ms > 0:
        output["comparison"]["file_vs_render"] = round(render_ms / file_ms)
    if render_ms > 0 and cache_ms > 0:
        output["comparison"]["cache_vs_render"] = round(render_ms / cache_ms)
    if file_ms > 0 and cache_ms > 0:
        output["comparison"]["cache_vs_file"] = round(file_ms / cache_ms)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n Results saved to: {output_path}")


# =============================================================================
# Main CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="F1 E-Ink Calendar Performance Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python scripts/benchmark_renderer.py
    uv run python scripts/benchmark_renderer.py --http
    uv run python scripts/benchmark_renderer.py --json data/benchmark.json
    uv run python scripts/benchmark_renderer.py --runs 100 -v
        """,
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of benchmark runs (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help=f"Number of warmup runs (default: {DEFAULT_WARMUP})",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Include HTTP endpoint benchmark (slower)",
    )
    parser.add_argument(
        "--json",
        type=str,
        metavar="PATH",
        help="Save results to JSON file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output with progress",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Skip memory profiling",
    )
    return parser.parse_args()


def main():
    """Run comprehensive benchmark suite."""
    args = parse_args()

    print_header()
    print(f"\n Configuration: {args.runs} runs, {args.warmup} warmup")
    if args.http:
        print(" HTTP endpoint benchmark: ENABLED")

    results: dict[str, Any] = {}

    # 1. Render benchmark (needed for other tests)
    print("\n Running render benchmark...")
    results["render"] = benchmark_render(args.runs, args.warmup, args.verbose)
    print_benchmark_result("ON-THE-FLY RENDER (full PIL pipeline)", results["render"], 1)

    # Get BMP data for cache test
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(get_mock_race_data(), get_mock_historical_data())

    # 2. Memory cache benchmark
    print("\n Running cache benchmark...")
    results["cache"] = benchmark_memory_cache(bmp_data, args.runs * 10, args.warmup)
    print_benchmark_result("IN-MEMORY CACHE (dict lookup)", results["cache"], 2)

    # 3. File read benchmark
    print("\n Running file read benchmark...")
    images_path = Path(__file__).parent.parent / "data" / "images"
    file_path = images_path / "calendar_en.bmp"

    if file_path.exists():
        results["file"] = benchmark_file_read(file_path, args.runs * 2, args.warmup)
    else:
        # Create temp file for benchmark
        print("    Pre-generated file not found, using temp file...")
        temp_path = Path("/tmp/benchmark_calendar.bmp")
        temp_path.write_bytes(bmp_data)
        results["file"] = benchmark_file_read(temp_path, args.runs * 2, args.warmup)
        temp_path.unlink()

    print_benchmark_result("PRE-GENERATED FILE (disk read)", results["file"], 3)

    # 4. HTTP endpoint benchmark (optional)
    if args.http:
        print("\n Running HTTP endpoint benchmark...")
        results["http"] = benchmark_http_endpoint(args.runs, args.warmup, args.verbose)
        print_benchmark_result("HTTP ENDPOINT (FastAPI TestClient)", results["http"], 4)

    # Memory profiling
    memory: dict[str, Any] = {}
    cache_memory: dict[str, Any] = {}

    if not args.no_memory:
        print("\n Running memory profiling...")
        memory = measure_memory_usage(args.verbose)
        cache_memory = measure_cache_memory()
        print_memory_results(memory, cache_memory)

    # Comparison
    print_comparison(results)

    # Footer
    print_footer()

    # JSON export
    if args.json:
        json_path = Path(args.json)
        save_results_json(results, memory, cache_memory, json_path)

    return results


if __name__ == "__main__":
    main()
