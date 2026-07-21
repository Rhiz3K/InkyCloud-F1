"""Palette-parameterized source-art preprocessing for shipped BMP assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps

if TYPE_CHECKING:
    import numpy as np

from app.services.bwr_renderer import BwrColors
from app.services.bwry_renderer import BwryColors
from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.renderers import DISPLAY_TYPES
from app.services.spectra6_renderer import Spectra6Colors
from app.services.track_assets import (
    discover_track_source_stems,
    resolve_track_source_path,
    strip_track_variant_suffix,
)
from app.utils.atomic_io import atomic_save_image, atomic_write_bytes_sync
from app.utils.bmp import (
    RgbColor,
    encode_indexed_bmp_4bit,
    map_to_bwr_palette,
    map_to_bwry_palette,
    quantize_to_palette,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACK_MAX_WIDTH = 490
TRACK_MAX_HEIGHT = 280
TRACK_MONO_THRESHOLD = 200
TRACK_NON_WHITE_THRESHOLD = 245
FLAG_WIDTH = 87
FLAG_HEIGHT = 58
FLAG_MAX_COLORS = 6

PREPROCESS_PALETTES = tuple(
    "mono" if display == "1bit" else display for display in DISPLAY_TYPES
)


class PreprocessingError(RuntimeError):
    """Raised when a preprocessing batch has no inputs or partial failures."""


@dataclass(frozen=True, slots=True)
class PaletteSpec:
    """Paths, source preference, and colors for one output palette."""

    name: str
    track_variant: str
    track_output: str
    flag_output: str
    colors: list[RgbColor] | None


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Aggregate file counts and byte totals from one preprocessing batch."""

    processed: int
    failures: int
    input_bytes: int
    output_bytes: int


_PALETTE_SPECS = {
    "mono": PaletteSpec("mono", "bw", "tracks_processed", "flags_processed", None),
    "bwr": PaletteSpec("bwr", "bwr", "tracks_bwr", "flags_bwr", BwrColors.PALETTE),
    "bwry": PaletteSpec("bwry", "bwry", "tracks_bwry", "flags_bwry", BwryColors.PALETTE),
    "spectra6": PaletteSpec(
        "spectra6",
        "spectra6",
        "tracks_spectra6",
        "flags_spectra6",
        Spectra6Colors.PALETTE,
    ),
}


def get_palette_spec(palette: str) -> PaletteSpec:
    """Resolve a CLI palette name through the renderer-owned display enumeration."""
    if palette not in PREPROCESS_PALETTES:
        choices = ", ".join(PREPROCESS_PALETTES)
        raise ValueError(f"Unknown palette {palette!r}; expected one of: {choices}")
    return _PALETTE_SPECS[palette]


def _flatten_color_source(image: Image.Image, white: RgbColor, *, spectra: bool) -> Image.Image:
    """Flatten transparency while preserving the legacy palette-specific behavior."""
    transparent = (
        image.mode in ("RGBA", "LA", "P") or "transparency" in image.info
        if spectra
        else image.mode in ("RGBA", "P")
    )
    if transparent:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, white)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image if image.mode == "RGB" else image.convert("RGB")


def _crop_non_white(image: Image.Image) -> Image.Image:
    """Crop RGB whitespace using the historical 245-channel threshold."""
    pixels = image.load()
    if pixels is None:
        raise ValueError("Failed to access track pixels")
    min_x, min_y = image.width, image.height
    max_x = max_y = -1
    for y in range(image.height):
        for x in range(image.width):
            pixel = pixels[x, y]  # type: ignore[index]
            if isinstance(pixel, tuple):
                red, green, blue = (int(channel) for channel in pixel[:3])
            else:
                red = green = blue = int(pixel)
            if min(red, green, blue) < TRACK_NON_WHITE_THRESHOLD:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x >= 0 and max_y >= 0:
        return image.crop((min_x, min_y, max_x + 1, max_y + 1))
    return image


def _fit_track(image: Image.Image) -> Image.Image:
    """Downscale a track to its runtime bounding box without upscaling."""
    width, height = image.size
    ratio = min(TRACK_MAX_WIDTH / width, TRACK_MAX_HEIGHT / height)
    if ratio >= 1:
        return image
    return image.resize(
        (int(width * ratio), int(height * ratio)),
        Image.Resampling.LANCZOS,
    )


def _map_color_palette(image: Image.Image, spec: PaletteSpec) -> Image.Image:
    """Map an RGB asset to the selected renderer palette."""
    if spec.colors is None:
        raise ValueError("A color palette is required")
    if spec.name == "bwr":
        return map_to_bwr_palette(image, spec.colors)
    if spec.name == "bwry":
        return map_to_bwry_palette(image, spec.colors)
    return quantize_to_palette(image, spec.colors, colors=len(spec.colors))


def _write_color_bmp(output_path: Path, image: Image.Image, spec: PaletteSpec) -> None:
    """Atomically encode a fixed-palette image as the shared 4-bit BMP format."""
    if spec.colors is None:
        raise ValueError("A color palette is required")
    atomic_write_bytes_sync(output_path, encode_indexed_bmp_4bit(image, spec.colors))


def process_track_image(input_path: Path, output_path: Path, palette: str) -> dict[str, Any]:
    """Process one track source into the selected display palette."""
    spec = get_palette_spec(palette)
    input_size = input_path.stat().st_size
    with Image.open(input_path) as opened:
        original_dimensions = opened.size
        if spec.name == "mono":
            gray = opened.convert("L")
            crop_mask = ImageOps.invert(
                gray.point(lambda pixel: 255 if pixel > 128 else 0)  # type: ignore[operator]
            )
            bbox = crop_mask.getbbox()
            prepared = gray.crop(bbox) if bbox else gray
            prepared = _fit_track(prepared)
            final = prepared.point(  # type: ignore[assignment]
                lambda pixel: 255 if pixel > TRACK_MONO_THRESHOLD else 0  # type: ignore[operator]
            ).convert("1")
            atomic_save_image(output_path, final, image_format="BMP")
        else:
            white = spec.colors[1] if spec.colors is not None else (255, 255, 255)
            rgb = _flatten_color_source(opened, white, spectra=spec.name == "spectra6")
            final = _map_color_palette(_fit_track(_crop_non_white(rgb)), spec)
            _write_color_bmp(output_path, final, spec)

    output_size = output_path.stat().st_size
    return {
        "input_size": input_size,
        "output_size": output_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
        "compression_ratio": input_size / output_size if output_size else 0,
    }


def calculate_luminance(rgb: tuple[int, int, int]) -> float:
    """Calculate perceived Rec. 601 luminance normalized to zero through one."""
    red, green, blue = rgb
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0


def create_pattern_tile(pattern_name: str, tile_size: int = 6) -> np.ndarray:
    """Create one legacy monochrome flag pattern tile."""
    import numpy as np

    tile = np.full((tile_size, tile_size), 255, dtype=np.uint8)
    if pattern_name == "solid_black":
        tile[:, :] = 0
    elif pattern_name == "dense_crosshatch":
        for y in range(tile_size):
            for x in range(tile_size):
                if y % 2 == 0 or x % 2 == 0:
                    tile[y, x] = 0
    elif pattern_name == "vertical_lines":
        for x in range(0, tile_size, 2):
            tile[:, x] = 0
    elif pattern_name == "horizontal_lines":
        for y in range(0, tile_size, 2):
            tile[y, :] = 0
    elif pattern_name == "diagonal_lines":
        for index in range(tile_size):
            tile[index, index % tile_size] = 0
            tile[index, (index + 1) % tile_size] = 0
    elif pattern_name == "checkerboard":
        for y in range(tile_size):
            for x in range(tile_size):
                if (y // 2 + x // 2) % 2 == 0:
                    tile[y, x] = 0
    elif pattern_name == "sparse_dots":
        tile[1, 1] = tile[1, 4] = tile[4, 1] = tile[4, 4] = 0
    elif pattern_name == "very_sparse_dots":
        tile[2, 2] = 0
    return tile


PATTERN_POOL = (
    "dense_crosshatch",
    "vertical_lines",
    "horizontal_lines",
    "diagonal_lines",
    "checkerboard",
    "sparse_dots",
    "very_sparse_dots",
)


def quantize_colors(
    image: Image.Image,
    n_colors: int = FLAG_MAX_COLORS,
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Quantize a flag with the deterministic legacy K-Means settings."""
    import numpy as np
    from sklearn.cluster import KMeans

    image_array = np.array(image)
    height, width = image_array.shape[:2]
    pixels = image_array.reshape(-1, 3)
    unique_colors = len(np.unique(pixels, axis=0))
    actual_colors = max(2, min(n_colors, unique_colors, len(pixels) // 10 + 1))
    kmeans = KMeans(n_clusters=actual_colors, random_state=42, n_init=10)
    labels = kmeans.fit_predict(pixels).reshape(height, width)
    centroids = kmeans.cluster_centers_.astype(int)
    return labels, [
        (int(color[0]), int(color[1]), int(color[2])) for color in centroids
    ]


def analyze_colors(labels: np.ndarray, centroids: list[tuple[int, int, int]]) -> list[dict]:
    """Describe each quantized flag color by area and luminance."""
    import numpy as np

    total_pixels = labels.size
    return [
        {
            "index": index,
            "rgb": rgb,
            "luminance": calculate_luminance(rgb),
            "area": np.sum(labels == index) / total_pixels,
        }
        for index, rgb in enumerate(centroids)
    ]


def assign_patterns(colors: list[dict]) -> dict[int, str]:
    """Assign deterministic monochrome patterns by luminance and area."""
    if not colors:
        return {}
    by_luminance = sorted(colors, key=lambda color: color["luminance"])
    darkest = by_luminance[0]
    bright = [color for color in colors if color["luminance"] > 0.5]
    brightest_dominant = (
        max(bright, key=lambda color: color["area"]) if bright else by_luminance[-1]
    )
    if darkest["index"] == brightest_dominant["index"]:
        pattern = "solid_white" if darkest["luminance"] > 0.5 else "solid_black"
        return {darkest["index"]: pattern}
    assignments = {
        darkest["index"]: "solid_black",
        brightest_dominant["index"]: "solid_white",
    }
    intermediate = [color for color in by_luminance if color["index"] not in assignments]
    for index, color in enumerate(intermediate):
        assignments[color["index"]] = PATTERN_POOL[min(index, len(PATTERN_POOL) - 1)]
    return assignments


def apply_pattern(image: np.ndarray, mask: np.ndarray, pattern_name: str) -> np.ndarray:
    """Apply a repeating monochrome tile to one quantized color region."""
    import numpy as np

    tile = create_pattern_tile(pattern_name)
    tile_height, tile_width = tile.shape
    image_height, image_width = image.shape
    pattern = np.tile(
        tile,
        (image_height // tile_height + 1, image_width // tile_width + 1),
    )[:image_height, :image_width]
    image[mask] = pattern[mask]
    return image


def _process_mono_flag(source: Image.Image) -> tuple[Image.Image, list[dict]]:
    """Render one RGB flag through the legacy luminance-pattern algorithm."""
    import numpy as np

    resized = source.resize((FLAG_WIDTH, FLAG_HEIGHT), Image.Resampling.LANCZOS)
    labels, centroids = quantize_colors(resized)
    colors = analyze_colors(labels, centroids)
    assignments = assign_patterns(colors)
    output = np.full((FLAG_HEIGHT, FLAG_WIDTH), 255, dtype=np.uint8)
    mappings: list[dict] = []
    for color in colors:
        pattern = assignments.get(color["index"], "solid_white")
        apply_pattern(output, labels == color["index"], pattern)
        mappings.append({**color, "pattern": pattern})
    output = np.where(output > 127, 255, 0).astype(np.uint8)
    return Image.fromarray(output, mode="L").convert("1"), mappings


def process_flag_image(input_path: Path, output_path: Path, palette: str) -> dict[str, Any]:
    """Process one source flag into the selected display palette."""
    spec = get_palette_spec(palette)
    input_size = input_path.stat().st_size
    with Image.open(input_path) as opened:
        original_dimensions = opened.size
        if spec.name == "mono":
            source = _flatten_color_source(opened, (255, 255, 255), spectra=False)
            final, mappings = _process_mono_flag(source)
            atomic_save_image(output_path, final, image_format="BMP")
        else:
            white = spec.colors[1] if spec.colors is not None else (255, 255, 255)
            source = _flatten_color_source(opened, white, spectra=spec.name == "spectra6")
            resized = source.resize((FLAG_WIDTH, FLAG_HEIGHT), Image.Resampling.LANCZOS)
            final = _map_color_palette(resized, spec)
            mappings = []
            _write_color_bmp(output_path, final, spec)
    result: dict[str, Any] = {
        "input_size": input_size,
        "output_size": output_path.stat().st_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
    }
    if spec.name == "mono":
        result.update(color_mappings=mappings, num_colors=len(mappings))
    return result


def preprocess_tracks(
    palette: str,
    circuits: list[str] | None = None,
    *,
    source_dir: Path | None = None,
    output_dir: Path | None = None,
) -> BatchResult:
    """Preprocess selected or all track sources for one palette."""
    spec = get_palette_spec(palette)
    source = source_dir or PROJECT_ROOT / "artwork" / "tracks"
    destination = output_dir or PROJECT_ROOT / "app" / "assets" / spec.track_output
    if not source.is_dir():
        raise PreprocessingError(f"Input directory not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    stems = discover_track_source_stems(source)
    if circuits:
        wanted = {circuit.strip().lower() for circuit in circuits if circuit.strip()}
        stems = [stem for stem in stems if stem in wanted]
    files = [
        path
        for stem in stems
        if (path := resolve_track_source_path(source, [stem], variant_suffix=spec.track_variant))
        is not None
    ]
    if not files:
        raise PreprocessingError(f"No track images found in {source}")
    return _run_batch(
        files,
        destination,
        palette,
        _track_output_stem,
        process_track_image,
    )


def _track_output_stem(path: Path) -> str:
    """Normalize source and palette suffixes to the runtime circuit identifier."""
    source_stem = strip_track_variant_suffix(path.stem)
    return CIRCUIT_ID_MAP.get(source_stem, source_stem)


def preprocess_flags(
    palette: str,
    *,
    source_dir: Path | None = None,
    output_dir: Path | None = None,
) -> BatchResult:
    """Preprocess all flat flag sources for one palette."""
    spec = get_palette_spec(palette)
    source = source_dir or PROJECT_ROOT / "app" / "assets" / "flags_flat"
    destination = output_dir or PROJECT_ROOT / "app" / "assets" / spec.flag_output
    if not source.is_dir():
        raise PreprocessingError(f"Input directory not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    files = sorted(source.glob("*.png"))
    if not files:
        raise PreprocessingError(f"No flag images found in {source}")
    return _run_batch(files, destination, palette, lambda path: path.stem, process_flag_image)


def _run_batch(
    files: list[Path],
    output_dir: Path,
    palette: str,
    output_stem: Any,
    processor: Any,
) -> BatchResult:
    """Run one preprocessing batch while preserving per-file failure isolation."""
    input_bytes = output_bytes = failures = 0
    for source_path in sorted(files):
        destination = output_dir / f"{output_stem(source_path)}.bmp"
        try:
            stats = processor(source_path, destination, palette)
            input_bytes += stats["input_size"]
            output_bytes += stats["output_size"]
            print(f" {source_path.name:25} -> {destination.name:25}")
        except Exception as exc:
            failures += 1
            print(f" {source_path.name:25} -> ERROR: {exc}")
    result = BatchResult(len(files) - failures, failures, input_bytes, output_bytes)
    if failures:
        raise PreprocessingError(f"{failures} of {len(files)} assets failed")
    print(f"Processed {result.processed} {palette} assets into {output_dir}")
    return result
