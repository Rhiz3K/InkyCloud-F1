# BMP Processing Pipeline

This document records the current image-to-BMP pipeline used in this repository for all display variants.

It covers:

- source asset naming conventions
- track preprocessing scripts
- flag preprocessing scripts
- runtime asset resolution order
- final calendar/team BMP encoding behavior
- currently active display variants

## 1. Display Variants

The project currently works with these display modes:

- `1bit` - monochrome black/white
- `bwr` - black/white/red
- `spectra6` - black/white/red/yellow/blue/green
- `bwry` - black/white/red/yellow

## 2. Source Asset Naming Convention

Track source images live in `app/assets/tracks/`.

The current naming convention is:

- `<circuit>_bw.png` - preferred source for `1bit`
- `<circuit>_bwr.png` - preferred source for `bwr`
- `<circuit>_spectra6.png` - preferred source for `spectra6`
- `<circuit>_bwry.png` - preferred source for `bwry`
- `<circuit>.png` - generic fallback if no display-specific source exists

Examples:

- `suzuka_bw.png`
- `suzuka_bwr.png`
- `suzuka_spectra6.png`
- `suzuka_bwry.png`
- `suzuka.png`

Important rule:

- plain `<circuit>.png` is fallback only
- display-specific source artwork should be preferred whenever it exists

Track variant resolution helpers live in `app/services/track_assets.py`.

## 3. Track Source Discovery and Canonical Stems

`app/services/track_assets.py` provides the shared logic used by preprocessing and runtime renderers.

Current responsibilities:

- normalize stems to lowercase and underscore-separated names
- strip known suffixes: `_bw`, `_bwr`, `_bwry`, `_spectra6`
- discover canonical track stems from `app/assets/tracks/`
- resolve source files in this order:
  - preferred display-specific variant
  - plain fallback source

Known circuit ID remapping is still handled renderer-side through `CIRCUIT_ID_MAP`, for example:

- `vegas` -> `las_vegas`

## 4. Runtime Track Loading Order

The runtime renderers no longer prefer preprocessed track BMPs first.

This was changed because text, labels, and thin lines look better when the renderer scales the original source artwork first and only then converts the final composed image to the target display format.

### 4.1 `1bit`

File: `app/services/renderer.py`

Current order:

1. `<circuit>_bw.(png|jpg|jpeg)`
2. `<circuit>.(png|jpg|jpeg)`
3. `app/assets/tracks_processed/<circuit>.bmp`
4. emergency fallback to the first available processed track BMP

Notes:

- `CIRCUIT_ID_MAP` is applied before candidate stems are built
- `circuit.location` is also used as a fallback candidate stem

### 4.2 `bwr`

File: `app/services/bwr_renderer.py`

Current order:

1. `<circuit>_bwr.(png|jpg|jpeg)`
2. `<circuit>.(png|jpg|jpeg)`
3. `app/assets/tracks_bwr/<circuit>.bmp`

This is especially important for text quality, because the original red/black source survives scaling better than a preprocessed BMP.

### 4.3 `spectra6`

File: `app/services/spectra6_renderer.py`

Current order:

1. `<circuit>_spectra6.(png|jpg|jpeg)`
2. `<circuit>.(png|jpg|jpeg)`
3. `app/assets/tracks_spectra6/<circuit>.bmp`

Again, source-first loading is intentional to preserve track text and thin colored lines.

### 4.4 `bwry`

File: `app/services/bwry_renderer.py`

Current order:

1. `<circuit>_bwry.(png|jpg|jpeg)`
2. `<circuit>.(png|jpg|jpeg)`
3. `app/assets/tracks_bwry/<circuit>.bmp`

## 5. Track Preprocessing Scripts

Preprocessed track BMPs are still kept as fallbacks and for future offline/asset workflows.

### 5.1 `1bit`

File: `scripts/preprocess_tracks.py`

Input selection:

- prefers `_bw` source
- falls back to plain source

Processing steps:

1. load source image
2. convert to grayscale
3. detect non-white bounding box and crop whitespace
4. resize to fit `490x280`
5. threshold to black/white (`THRESHOLD = 200`)
6. convert to mode `1`
7. save as `app/assets/tracks_processed/<circuit>.bmp`

### 5.2 `bwr`

File: `scripts/preprocess_tracks_bwr.py`

Input selection:

- prefers `_bwr` source
- falls back to plain source

Processing steps:

1. load source image and flatten transparency onto white
2. crop whitespace using `NON_WHITE_THRESHOLD = 245`
3. resize to fit `490x280`
4. map pixels with `map_to_bwr_palette(...)`
5. encode using indexed 4-bit BMP via `encode_indexed_bmp_4bit(...)`
6. save as `app/assets/tracks_bwr/<circuit>.bmp`

This script now uses the shared strict B/W/R palette mapping rather than a custom rough threshold pass.

### 5.3 `spectra6`

File: `scripts/preprocess_tracks_spectra6.py`

Input selection:

- prefers `_spectra6` source
- falls back to plain source

Processing steps:

1. load source image and flatten transparency onto white
2. crop whitespace using `NON_WHITE_THRESHOLD = 245`
3. resize to fit `490x280`
4. quantize directly to the `Spectra6Colors.PALETTE`
5. encode as indexed 4-bit BMP via `encode_indexed_bmp_4bit(...)`
6. save as `app/assets/tracks_spectra6/<circuit>.bmp`

### 5.4 `bwry`

File: `scripts/preprocess_tracks_bwry.py`

Input selection:

- prefers `_bwry` source
- falls back to plain source

Processing steps:

1. load source image and flatten transparency onto white
2. crop whitespace
3. resize to fit `490x280`
4. map pixels with `map_to_bwry_palette(...)`
5. encode as indexed 4-bit BMP
6. save as `app/assets/tracks_bwry/<circuit>.bmp`

This variant is now available in the public calendar display selection.

## 6. Final Full-Image BMP Encoding

The final screen BMP is not produced the same way for every display.

### 6.1 `1bit`

File: `app/services/renderer.py`

Current final render flow:

1. compose the full screen as a normal PIL image
2. paste track source artwork after scaling it into the layout
3. convert the whole final image to strict 1-bit BMP output

Key point:

- source artwork is intentionally scaled before final bitmap reduction

### 6.2 `bwr`

File: `app/services/bwr_renderer.py`

Current final render flow:

1. compose the full screen in RGB
2. use vivid red source assets where available
3. convert the full image with `map_to_bwr_palette(...)`
4. encode as indexed 4-bit BMP with palette `[BLACK, WHITE, RED]`

This means the final BMP conversion happens after the whole layout is composed, which is why source-first track loading improves text quality.

### 6.3 `spectra6`

File: `app/services/spectra6_renderer.py`

Current final render flow:

1. compose the full screen in RGB
2. paste track source artwork after scaling it into the layout
3. quantize/export using the Spectra 6 palette

### 6.4 `bwry`

File: `app/services/bwry_renderer.py`

Current final render flow:

1. compose the full screen in RGB
2. use `bwry` source assets where available
3. convert the full image with `map_to_bwry_palette(...)`
4. encode as indexed 4-bit BMP with palette `[BLACK, WHITE, RED, YELLOW]`

The same source-first reasoning applies here too.

### 6.5 `teams` dashboard

Files:

- `app/services/renderer.py`
- `app/services/spectra6_renderer.py`
- `app/services/bwr_renderer.py`
- `app/services/bwry_renderer.py`

Current teams render flow:

1. load source PNG assets for driver silhouettes and team logos from `app/assets/images/`
2. compose the full teams dashboard first
3. only then reduce the final image to the target display format

Per-display behavior:

- `1bit` keeps a monochrome output path, but now follows the same source-first rule as calendar tracks:
  - keep the cropped source logo in RGBA until draw time
  - resize from the original logo first
  - flatten onto white and threshold to `1bit` only after scaling
- `spectra6` composes in RGB and exports via Spectra 6 indexed BMP
- `bwr` composes in RGB and converts the final dashboard with `map_to_bwr_palette(...)`
- `bwry` composes in RGB and converts the final dashboard with `map_to_bwry_palette(...)`

This follows the same late-reduction rule as the calendar screen: preserve source detail
for logos, text edges, and small UI elements until the final export step.

Team logo source notes:

- default current-team color logos are downloaded by `scripts/download_team_logos.py` from Formula 1 media assets and stored in `app/assets/images/teams_color/`
- `audi` uses the explicit override `https://upload.wikimedia.org/wikipedia/commons/a/ae/Logo_audi.jpg`
- `cadillac` uses the explicit override `https://pngimg.com/d/cadillac_PNG42.png`
- color renderers prefer `app/assets/images/teams_color/`, while `1bit` also uses the same source set and reduces it during final monochrome rendering so logo sizing and centering stay aligned across displays
- `audi` and `cadillac` are cropped to their primary upper mark band so the lower wordmarks do not shrink the logo area
- `1bit` intentionally overrides some logos such as `ferrari`, `cadillac`, and `red_bull` with the dedicated monochrome assets from `app/assets/images/teams/` when those assets preserve shape/detail better than thresholding the color logo
- the `red_bull` 1bit override is sourced from `https://images.icon-icons.com/2845/PNG/512/redbull_logo_icon_181345.png`

## 7. Current Active Palettes

### 7.1 `bwr`

Current B/W/R palette:

- black: `#000000`
- white: `#FFFFFF`
- red: `#FF0000`

Relevant files:

- `app/services/bwr_renderer.py`
- `scripts/preprocess_tracks_bwr.py`
- `scripts/preprocess_flags_bwr.py`

### 7.2 `spectra6`

Current vivid Spectra 6 palette:

- black: `#000000`
- white: `#FFFFFF`
- red: `#FF0000`
- yellow: `#FFD800`
- green: `#00D800`
- blue: `#00A8FF`

Relevant file:

- `app/services/spectra6_renderer.py`

### 7.3 `bwry`

Current active palette:

- black: `#000000`
- white: `#FFFFFF`
- red: `#FF0000`
- yellow: `#FFD800`

Relevant file:

- `app/services/bwry_renderer.py`
- `scripts/preprocess_tracks_bwry.py`
- `scripts/preprocess_flags_bwry.py`

This can still be tuned later once more real hardware comparisons are available.

## 8. Flag Processing

Flags are handled differently from track maps.

### 8.1 `1bit` flags

File: `scripts/preprocess_flags.py`

This pipeline is pattern-based, not simple threshold-based.

High-level steps:

1. resize source flag to target size
2. quantize to a limited color set via K-Means
3. analyze luminance and area coverage
4. assign solid black/solid white/pattern fills to preserve structure in monochrome
5. save to `app/assets/flags_processed/*.bmp`

### 8.2 `bwr` flags

File: `scripts/preprocess_flags_bwr.py`

High-level steps:

1. flatten transparency onto white
2. resize to target size
3. classify red vs black vs white
4. quantize to the vivid B/W/R palette
5. save to `app/assets/flags_bwr/*.bmp`

### 8.3 `spectra6` flags

Current state:

- `app/assets/flags_spectra6/*.bmp` already exists as preprocessed assets
- there is currently no dedicated `preprocess_flags_spectra6.py` script in the repo

So Spectra 6 flag assets are present, but their regeneration pipeline is not yet documented in a dedicated script.

### 8.4 `bwry` flags

File: `scripts/preprocess_flags_bwry.py`

High-level steps:

1. flatten transparency onto white
2. resize to target size
3. map pixels with `map_to_bwry_palette(...)`
4. encode as indexed 4-bit BMP with the `B/W/R/Y` palette
5. save to `app/assets/flags_bwry/*.bmp`

## 9. Why Source-First Loading Matters

The biggest practical lesson from the recent changes is this:

- preprocessed track BMPs are fine as fallbacks
- but for the best final quality, especially text and thin colored lines, the renderer should use display-specific source artwork first

Why:

1. the source image keeps anti-aliased edges and finer detail longer
2. the renderer scales the asset directly inside the final layout
3. palette reduction happens later, on the final composed image
4. this avoids “double degradation” from:
   - first converting the asset to BMP
   - then scaling or reusing that already-reduced asset in the final screen

This is why all current active display modes now use:

- source artwork first
- preprocessed BMP second

## 10. Current Command Reference

Useful commands for the current pipeline:

```bash
# 1-bit tracks
.venv/bin/python scripts/preprocess_tracks.py --circuits suzuka

# B/W/R tracks
.venv/bin/python scripts/preprocess_tracks_bwr.py --circuits suzuka

# Spectra 6 tracks
.venv/bin/python scripts/preprocess_tracks_spectra6.py --circuits suzuka

# Prepared B/W/R/Y tracks
.venv/bin/python scripts/preprocess_tracks_bwry.py --circuits suzuka

# 1-bit flags
.venv/bin/python scripts/preprocess_flags.py

# B/W/R flags
.venv/bin/python scripts/preprocess_flags_bwr.py

# B/W/R/Y flags
.venv/bin/python scripts/preprocess_flags_bwry.py
```

## 11. Current Open Items

The following items are still intentionally unfinished or future-facing:

- dedicated preprocessing script for `flags_spectra6`
- possible palette tuning for `bwry`
- possible additional per-display art tuning once real hardware comparison is finished

## 12. Summary

Current project rule of thumb:

- keep per-display source artwork in `app/assets/tracks/`
- use display-specific suffixes (`_bw`, `_bwr`, `_spectra6`, `_bwry`)
- use plain `<circuit>.png` only as fallback
- let renderers prefer source artwork first
- keep preprocessed BMPs as fallback and cache-like assets
- do final display reduction as late as possible in the pipeline

That is the current BMP processing strategy across all active and prepared variants.
