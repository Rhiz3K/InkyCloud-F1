#!/usr/bin/env python3
"""Pre-process flag images for 1-bit E-Ink rendering using luminance-based patterns.

This script converts flag PNG images to 1-bit BMP files using a dynamic
pattern assignment based on color luminance and area coverage.

Processing steps:
1. Load original PNG and resize to target dimensions
2. Quantize colors to max 6 dominant colors using K-Means clustering
3. Analyze each color:
   - Calculate luminance (perceived brightness)
   - Calculate area coverage (percentage of pixels)
4. Apply dynamic mapping rules:
   - Darkest color -> Solid black
   - Brightest dominant color (largest area among light colors) -> Solid white
   - Intermediate colors -> Distinct patterns based on luminance rank
5. Save as strict 1-bit BMP

Pattern pool for intermediates (ordered by density, darkest first):
- Dense cross-hatch (darkest intermediate)
- Vertical lines
- Diagonal lines
- Checkerboard
- Sparse dots (lightest intermediate)

Usage:
    python scripts/preprocess_flags.py
"""

import sys
from pathlib import Path

try:
    import numpy as np
    from sklearn.cluster import KMeans
except ImportError as exc:
    message = (
        "Optional dependencies for flag preprocessing are missing. "
        "Install them with `pip install -e .[dev]`."
    )
    print(message, file=sys.stderr)
    raise SystemExit(1) from exc

from PIL import Image

# Constants
TARGET_WIDTH = 87
TARGET_HEIGHT = 58
MAX_COLORS = 6  # Maximum colors for quantization


# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
FLAGS_INPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_flat"
FLAGS_OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_processed"


def calculate_luminance(rgb: tuple[int, int, int]) -> float:
    """
    Calculate perceived luminance using standard formula.

    Uses the sRGB luminance formula which accounts for human perception.

    Args:
        rgb: Tuple of (R, G, B) values (0-255)

    Returns:
        Luminance value between 0.0 (black) and 1.0 (white)
    """
    r, g, b = rgb
    # Standard sRGB luminance coefficients
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def create_pattern_tile(pattern_name: str, tile_size: int = 6) -> np.ndarray:
    """
    Create a repeating pattern tile.

    Patterns are ordered by visual density (darkest to lightest):
    - dense_crosshatch: Very dense grid
    - vertical_lines: Vertical stripes
    - diagonal_lines: Diagonal stripes
    - checkerboard: Alternating pixels
    - sparse_dots: Few scattered dots

    Args:
        pattern_name: Name of the pattern
        tile_size: Size of the repeating tile

    Returns:
        numpy array of shape (tile_size, tile_size) with values 0 (black) or 255 (white)
    """
    tile = np.full((tile_size, tile_size), 255, dtype=np.uint8)

    if pattern_name == "solid_black":
        tile[:, :] = 0

    elif pattern_name == "solid_white":
        pass  # Already white

    elif pattern_name == "dense_crosshatch":
        # Dense grid - every other pixel in both directions
        for y in range(tile_size):
            for x in range(tile_size):
                if y % 2 == 0 or x % 2 == 0:
                    tile[y, x] = 0

    elif pattern_name == "vertical_lines":
        # Vertical lines with 1px black, 1px white
        for x in range(0, tile_size, 2):
            tile[:, x] = 0

    elif pattern_name == "horizontal_lines":
        # Horizontal lines with 1px black, 1px white
        for y in range(0, tile_size, 2):
            tile[y, :] = 0

    elif pattern_name == "diagonal_lines":
        # Diagonal lines (45 degrees)
        for i in range(tile_size):
            tile[i, i % tile_size] = 0
            tile[i, (i + 1) % tile_size] = 0

    elif pattern_name == "checkerboard":
        # Checkerboard pattern - alternating 2x2 blocks
        for y in range(tile_size):
            for x in range(tile_size):
                if (y // 2 + x // 2) % 2 == 0:
                    tile[y, x] = 0

    elif pattern_name == "sparse_dots":
        # Sparse dots - single pixels
        tile[1, 1] = 0
        tile[1, 4] = 0
        tile[4, 1] = 0
        tile[4, 4] = 0

    elif pattern_name == "very_sparse_dots":
        # Very sparse - just corner dots
        tile[2, 2] = 0

    return tile


# Ordered pool of patterns from darkest (most black) to lightest (most white)
PATTERN_POOL = [
    "dense_crosshatch",  # ~75% black
    "vertical_lines",  # ~50% black
    "horizontal_lines",  # ~50% black
    "diagonal_lines",  # ~33% black
    "checkerboard",  # ~50% black but visually lighter
    "sparse_dots",  # ~11% black
    "very_sparse_dots",  # ~3% black
]


def quantize_colors(image: Image.Image, n_colors: int = MAX_COLORS) -> tuple[np.ndarray, list]:
    """
    Quantize image colors using K-Means clustering.

    Args:
        image: PIL Image in RGB mode
        n_colors: Maximum number of colors

    Returns:
        Tuple of (label array, list of RGB centroids)
    """
    img_array = np.array(image)
    h, w = img_array.shape[:2]
    pixels = img_array.reshape(-1, 3)

    # Use fewer clusters if image has fewer unique colors
    unique_colors = len(np.unique(pixels, axis=0))
    actual_n_colors = min(n_colors, unique_colors, len(pixels) // 10 + 1)
    actual_n_colors = max(2, actual_n_colors)  # At least 2 colors

    kmeans = KMeans(n_clusters=actual_n_colors, random_state=42, n_init=10)
    labels = kmeans.fit_predict(pixels)
    centroids = kmeans.cluster_centers_.astype(int)

    labels = labels.reshape(h, w)
    return labels, [tuple(c) for c in centroids]


def analyze_colors(labels: np.ndarray, centroids: list[tuple[int, int, int]]) -> list[dict]:
    """
    Analyze colors by luminance and area coverage.

    Args:
        labels: 2D array of color indices
        centroids: List of RGB tuples

    Returns:
        List of dicts with 'rgb', 'luminance', 'area', 'index' for each color
    """
    total_pixels = labels.size
    colors = []

    for i, rgb in enumerate(centroids):
        area = np.sum(labels == i) / total_pixels
        luminance = calculate_luminance(rgb)
        colors.append(
            {
                "index": i,
                "rgb": rgb,
                "luminance": luminance,
                "area": area,
            }
        )

    return colors


def assign_patterns(colors: list[dict]) -> dict[int, str]:
    """
    Assign patterns to colors based on luminance and area coverage.

    Rules:
    1. Darkest color -> Solid black
    2. Brightest color with largest area among bright colors -> Solid white
    3. Remaining colors -> Patterns based on luminance rank

    Args:
        colors: List of color analysis dicts

    Returns:
        Dict mapping color index to pattern name
    """
    if not colors:
        return {}

    # Sort by luminance
    sorted_by_lum = sorted(colors, key=lambda c: c["luminance"])

    # Rule 1: Darkest color -> Solid black
    darkest = sorted_by_lum[0]

    # Rule 2: Among bright colors (luminance > 0.5), find one with largest area
    bright_colors = [c for c in colors if c["luminance"] > 0.5]
    if bright_colors:
        brightest_dominant = max(bright_colors, key=lambda c: c["area"])
    else:
        # If no bright colors, use the lightest one
        brightest_dominant = sorted_by_lum[-1]

    # Assign solid black and white
    assignments = {
        darkest["index"]: "solid_black",
        brightest_dominant["index"]: "solid_white",
    }

    # Rule 3: Intermediate colors get patterns based on luminance rank
    intermediates = [c for c in sorted_by_lum if c["index"] not in assignments]

    # Assign patterns - darker intermediates get denser patterns
    for i, color in enumerate(intermediates):
        pattern_idx = min(i, len(PATTERN_POOL) - 1)
        assignments[color["index"]] = PATTERN_POOL[pattern_idx]

    return assignments


def apply_pattern(image_array: np.ndarray, mask: np.ndarray, pattern_name: str) -> np.ndarray:
    """
    Apply a pattern to masked regions of an image.

    Args:
        image_array: Output image array (modified in place)
        mask: Boolean mask where pattern should be applied
        pattern_name: Name of pattern to apply

    Returns:
        Modified image array
    """
    tile = create_pattern_tile(pattern_name)
    tile_h, tile_w = tile.shape
    img_h, img_w = image_array.shape

    # Create tiled pattern for entire image
    pattern = np.tile(tile, (img_h // tile_h + 1, img_w // tile_w + 1))
    pattern = pattern[:img_h, :img_w]

    # Apply pattern only where mask is True
    image_array[mask] = pattern[mask]

    return image_array


def process_flag_image(input_path: Path, output_path: Path) -> dict:
    """
    Process a single flag image to 1-bit BMP.

    Args:
        input_path: Path to input PNG file
        output_path: Path to output BMP file

    Returns:
        Dictionary with processing stats
    """
    # Load original
    original = Image.open(input_path)
    original_size = input_path.stat().st_size

    # Convert to RGB (handle transparency)
    if original.mode in ("RGBA", "P"):
        background = Image.new("RGB", original.size, (255, 255, 255))
        if original.mode == "P":
            original = original.convert("RGBA")
        if original.mode == "RGBA":
            background.paste(
                original, mask=original.split()[3] if len(original.split()) == 4 else None
            )
            original = background
    elif original.mode != "RGB":
        original = original.convert("RGB")

    # Resize to target dimensions
    resized = original.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)

    # Quantize colors
    labels, centroids = quantize_colors(resized)

    # Analyze colors
    colors = analyze_colors(labels, centroids)

    # Assign patterns dynamically
    assignments = assign_patterns(colors)

    # Create output array
    output = np.full((TARGET_HEIGHT, TARGET_WIDTH), 255, dtype=np.uint8)

    # Apply patterns to each color region
    color_mappings = []
    for color in colors:
        idx = color["index"]
        pattern = assignments.get(idx, "solid_white")
        mask = labels == idx
        apply_pattern(output, mask, pattern)
        color_mappings.append(
            {
                "rgb": color["rgb"],
                "luminance": color["luminance"],
                "area": color["area"],
                "pattern": pattern,
            }
        )

    # Convert to PIL Image in mode '1' (strict 1-bit)
    output = np.where(output > 127, 255, 0).astype(np.uint8)
    final = Image.fromarray(output, mode="L").convert("1")

    # Save as BMP
    final.save(output_path, format="BMP")
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": Image.open(input_path).size,
        "final_dimensions": final.size,
        "color_mappings": color_mappings,
        "num_colors": len(colors),
    }


def main():
    """Process all flag images."""
    print("=" * 70)
    print(" Flag Image Pre-processor (1-bit BMP, luminance-based patterns)")
    print("=" * 70)

    if not FLAGS_INPUT_DIR.exists():
        print(f"Input directory not found: {FLAGS_INPUT_DIR}")
        sys.exit(1)

    FLAGS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    flag_files = list(FLAGS_INPUT_DIR.glob("*.png"))

    if not flag_files:
        print(f"No flag images found in {FLAGS_INPUT_DIR}")
        sys.exit(1)

    print(f"Found {len(flag_files)} flag images")
    print(f"Target size: {TARGET_WIDTH}x{TARGET_HEIGHT} pixels")
    print(f"Max colors: {MAX_COLORS}")
    print("-" * 70)
    print(f"{'Flag':6} | {'Colors':6} | {'Pattern Assignments':<50}")
    print("-" * 70)

    total_input_size = 0
    total_output_size = 0

    for flag_path in sorted(flag_files):
        output_path = FLAGS_OUTPUT_DIR / f"{flag_path.stem}.bmp"

        try:
            stats = process_flag_image(flag_path, output_path)
            total_input_size += stats["input_size"]
            total_output_size += stats["output_size"]

            # Format color mappings
            mappings = []
            for m in sorted(stats["color_mappings"], key=lambda x: -x["luminance"]):
                lum = f"L{m['luminance']:.2f}"
                area = f"{m['area'] * 100:.0f}%"
                pat = m["pattern"].replace("_", " ")[:12]
                mappings.append(f"{lum}({area})->{pat}")

            mappings_str = " | ".join(mappings[:4])  # Show max 4
            print(f"{flag_path.stem:6} | {stats['num_colors']:6} | {mappings_str}")

        except Exception as e:
            print(f"{flag_path.stem:6} | ERROR: {e}")

    print("-" * 70)
    print(f"Total: {total_input_size / 1024:.1f}KB -> {total_output_size / 1024:.1f}KB")
    print("=" * 70)
    print(f"\nProcessed flags saved to: {FLAGS_OUTPUT_DIR}")
    print("\nPattern legend (ordered by density, dark to light):")
    print("  solid_black     = 100% black    dense_crosshatch = ~75% black")
    print("  vertical_lines  = ~50% black    horizontal_lines = ~50% black")
    print("  diagonal_lines  = ~33% black    checkerboard     = ~50% black")
    print("  sparse_dots     = ~11% black    solid_white      = 0% black")


if __name__ == "__main__":
    main()
