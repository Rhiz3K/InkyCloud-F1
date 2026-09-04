# BMP Processing Pipeline

Editable circuit artwork and shipped runtime BMPs are intentionally separate. Source PNG/PSD
files live under `artwork/` and are excluded from wheels and container images; the application
loads only compact processed assets under `app/assets/`.

The command reference and weekly maintainer checklist are also available in
[`scripts/README.md`](./scripts/README.md).

## Display palettes

| CLI palette | API `display` | Track output | Flag output |
| --- | --- | --- | --- |
| `mono` | `1bit` | `app/assets/tracks_processed/` | `app/assets/flags_processed/` |
| `bwr` | `bwr` | `app/assets/tracks_bwr/` | `app/assets/flags_bwr/` |
| `bwry` | `bwry` | `app/assets/tracks_bwry/` | `app/assets/flags_bwry/` |
| `spectra6` | `spectra6` | `app/assets/tracks_spectra6/` | `app/assets/flags_spectra6/` |

The CLI palette enumeration is derived from `app.services.renderers.DISPLAY_TYPES`, so routing,
rendering, scheduling, documentation, and preprocessing cannot silently drift apart.

## Track sources

Official F1 artwork is acquired as an explicit maintainer step. The importer never makes a
network request: download the original PNG only after confirming that its use is permitted, then
record its official page, versioned source URL, SHA-256, dimensions, palette profile, and
sector-boundary metadata in `artwork/tracks/sources.json`. Formula 1's
[legal notices](https://www.formula1.com/en/information/legal-notices.7egvZU48hzrypubGBNcQKt)
and [brand guidelines](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt)
remain the source of truth for usage rights.

Import a locally reviewed original and regenerate all runtime variants in one command:

```bash
uv run python -m scripts.manage import track \
  --source /path/to/original.png \
  --circuit sepang \
  --preprocess
```

`--expected-sha256` is an optional second check against the required manifest hash. Validation,
transformation, and PNG encoding complete before publication. The importer invalidates the old
bundle marker first, atomically replaces the five PNGs, and publishes
`<circuit-id>.bundle.json` last. That marker binds the source hash to the exact hashes of all five
files. A validation or encoding error leaves the reviewed files untouched; an interrupted
publication cannot leave a valid marker, so preprocessing and CI reject the partial bundle. The
provenance record documents the human rights review; it does not itself grant permission to reuse
the source.

Generic source art uses:

```text
artwork/tracks/<circuit-id>.png
```

An optional display-specific source takes precedence for that palette:

```text
artwork/tracks/<circuit-id>_bw.png
artwork/tracks/<circuit-id>_bwr.png
artwork/tracks/<circuit-id>_bwry.png
artwork/tracks/<circuit-id>_spectra6.png
```

PSD files may accompany PNG sources but are never read by preprocessing. Known variant suffixes
are stripped before the output filename is derived. Circuit aliases use the shared
`CIRCUIT_ID_MAP` (`vegas` becomes runtime key `las_vegas`).

Runtime renderers do not read `artwork/`. Each renderer loads `<circuit-id>.bmp` only from its
dedicated processed directory. Missing art produces the normal track placeholder rather than a
source-file lookup inside production.

## Track algorithms

The local importer recognizes both official F1 source generations: legacy
magenta/yellow/cyan sector strokes and modern magenta/yellow/blue strokes. It composites
transparency onto white, preserves the neutral artwork, and applies semantic sector mappings
rather than globally quantizing colors:

| Source sector | `mono` | `bwr` | `bwry` | `spectra6` |
| --- | --- | --- | --- | --- |
| S1 (magenta) | white | white | red | red |
| S2 (yellow) | white | white | white | blue |
| S3 (blue/cyan) | white | white | yellow | yellow |

When all sector strokes collapse to one color, the two normalized boundaries from the manifest
become perpendicular separators: white for `mono`, red for `bwr`. The source variants remain
reviewable PNGs; the existing preprocessing stage then converts them into the strict device
palettes below.

All palettes use the single `process_track_image(..., palette)` implementation in
`app/services/asset_preprocessing.py`.

- `mono`: grayscale, whitespace crop, 490×280 fit, threshold 200, strict 1-bit BMP
- `bwr`: alpha flattening, non-white crop, 490×280 fit, fixed B/W/R mapping, 4-bit indexed BMP
- `bwry`: the same shared pipeline with fixed B/W/R/Y mapping
- `spectra6`: the same shared pipeline with fixed six-color quantization

Color outputs use the palette classes owned by their renderers and the common deterministic
4-bit BMP encoder. This prevents a script palette from differing from the HTTP renderer.

## Flag sources and algorithms

Flat PNG sources live in `app/assets/flags_flat/` and are resized to 87×58.

- `mono` retains the luminance-ranked K-Means pattern algorithm for a strict 1-bit result.
- color modes flatten transparency onto palette white, use the same BWR/BWRY/fixed-palette
  mapping as track and full-screen rendering, and use the shared 4-bit encoder.

NumPy and scikit-learn are development dependencies required by monochrome flag preprocessing.
Install them through the locked development group, not an ad-hoc `pip install`.

## Unified commands

Prepare the environment once:

```bash
uv sync --locked --group dev
```

Regenerate one circuit in every palette:

```bash
uv run python -m scripts.manage preprocess tracks --palette mono --circuits suzuka
uv run python -m scripts.manage preprocess tracks --palette bwr --circuits suzuka
uv run python -m scripts.manage preprocess tracks --palette bwry --circuits suzuka
uv run python -m scripts.manage preprocess tracks --palette spectra6 --circuits suzuka
```

Omit `--circuits` to regenerate every discovered circuit. Multiple IDs are comma-separated.

Regenerate flags:

```bash
uv run python -m scripts.manage preprocess flags --palette mono
uv run python -m scripts.manage preprocess flags --palette bwr
uv run python -m scripts.manage preprocess flags --palette bwry
uv run python -m scripts.manage preprocess flags --palette spectra6
```

The eight former `scripts/preprocess_*.py` names remain thin compatibility wrappers for one
transition period. New automation must use `python -m scripts.manage`.

## Weekly track workflow

1. Download the original PNG manually from the reviewed official race page and compute its
   SHA-256; do not treat a Cloudinary version segment as a permanent archive.
2. Add or update its entry in `artwork/tracks/sources.json`, including two normalized separator
   points and their normal angles.
3. Run `scripts.manage import track ... --preprocess`; keep any PSD working files under
   `artwork/tracks/` too.
4. Start the app and inspect `/configure/calendar` in all four display modes. Pay particular
   attention to antialiasing, sector labels, callouts, and separator placement.
5. Run `uv run python scripts/check_track_assets.py` and the renderer regression suite.
6. Commit the provenance manifest, generated bundle marker, source variants, and processed
   runtime BMPs together after visual review.

CI first verifies each manifest-managed source bundle against its final marker, then regenerates
every discoverable track into a temporary directory and compares it byte-for-byte with the shipped
runtime BMP. Commit the marker and regenerated `app/assets/tracks_*` outputs together with every
source-art change; otherwise the asset tests fail with the exact invalid or stale filenames.

The season-update workflow also checks every active current/next-season circuit for rebuildable
source artwork and all four runtime BMPs. A missing circuit keeps the update PR available for
review but fails the workflow, which activates the repository's persistent workflow-failure
issue. Upstream artwork changes are never accepted or merged automatically; update the manifest
hash and rerun the local import only after reviewing the new original.

This workflow is intentionally separate from ordinary application releases. Source artwork is
not copied into the wheel or Docker build context.

## Byte compatibility

`tests/test_asset_preprocessing.py` locks deterministic synthetic inputs to the exact hashes
produced by the pre-consolidation scripts for every track and flag palette. Full-screen calendar
and teams renderers have separate golden SHA-256 tests for all four displays.

When a dependency upgrade intentionally changes rasterization, update processed assets and golden
hashes only after a visual review on representative real panels.
