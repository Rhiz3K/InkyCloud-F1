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

1. Add or edit the source PNG under `artwork/tracks/`; keep PSD working files there too.
2. Run all four track commands for the affected circuit.
3. Start the app and inspect `/configure/calendar` in all four display modes.
4. Test a circuit with specific source variants and a circuit using the generic source fallback.
5. Review binary changes and run the renderer regression suite.
6. Commit source artwork and processed runtime BMPs together when artwork itself is the task.

This workflow is intentionally separate from ordinary application releases. Source artwork is
not copied into the wheel or Docker build context.

## Byte compatibility

`tests/test_asset_preprocessing.py` locks deterministic synthetic inputs to the exact hashes
produced by the pre-consolidation scripts for every track and flag palette. Full-screen calendar
and teams renderers have separate golden SHA-256 tests for all four displays.

When a dependency upgrade intentionally changes rasterization, update processed assets and golden
hashes only after a visual review on representative real panels.
