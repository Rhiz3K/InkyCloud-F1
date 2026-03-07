# Track Conversion Log

This document tracks iterative work on F1 track source conversion, especially
for `albert_park` and the 1-bit readability tuning workflow.

## Current Status (2026-03-05)

- Source image is taken from F1 CDN and stored in `app/assets/tracks/albert_park.png`.
- 1-bit output is in `app/assets/tracks_processed/albert_park.bmp`.
- Spectra6 output is in `app/assets/tracks_spectra6/albert_park.bmp`.
- Current tuning target for 1-bit:
  - white colored track accents
  - black label backgrounds under text
  - white text inside those black label backgrounds

## Saved Scripts

- `scripts/track_conversion_utils.py`
  - shared conversion helpers
  - color-aware 1-bit conversion
  - Spectra6 conversion
  - rendered output metrics + scoring helpers
- `scripts/convert_track_assets.py`
  - optional source download
  - 1-bit and Spectra6 conversion in one command
- `scripts/search_track_1bit_params.py`
  - random search over conversion params
  - scores final rendered output from `/calendar.bmp`
  - stores best candidate outputs to `/tmp`
- `scripts/search_track_1bit_layered_parallel.py`
  - layered segmentation strategy instead of plain threshold-first tuning
  - uses `ProcessPoolExecutor` across all CPU cores
  - ranks candidates by local preview metrics, then verifies finalists against the live render endpoint
  - supports local fine-tuning around a saved best-params JSON seed
- `scripts/score_track_render.py`
  - quick semantic quality score of current rendered endpoint output

## Workflow

1. Update source and regenerate assets:

```bash
python scripts/convert_track_assets.py --circuit-id albert_park
```

If you want to fetch the current default F1 source URL first:

```bash
python scripts/convert_track_assets.py --circuit-id albert_park --download-default-url
```

2. Score current output from running server:

```bash
python scripts/score_track_render.py --tz Europe/Prague
```

3. Search better 1-bit params against rendered output:

```bash
python scripts/search_track_1bit_params.py --trials 300 --seed 20260305
```

For the newer layered multi-core search:

```bash
python scripts/search_track_1bit_layered_parallel.py --trials 10000 --workers 16 --finalists 192
```

For local fine-tuning around a previous winner:

```bash
python scripts/search_track_1bit_layered_parallel.py \
  --trials 20000 \
  --workers 16 \
  --finalists 256 \
  --base-params-file /tmp/albert_park_layered_best_10000.json \
  --local-scale 0.4
```

4. Validate top candidate in browser/Playwright only after script scoring.

## Notes on Quality Comparison (without Playwright)

When Playwright is unstable, use scripted quality checks against rendered output:

- black pixel ratio in map region
- largest connected component size/fill ratio
- count of compact box-like components (label backgrounds)
- white ratio inside those boxes (text readability proxy)
- small-component noise count

This is more robust than comparing raw BMP bytes or image hashes directly.

Additional comparisons now used during tuning:

- local preview score on a centered 500x268 track canvas
- finalist verification score from the live `/calendar.bmp` endpoint
- baseline comparison against the current default conversion pipeline
- box readability proxy (`box_white_ratio`) plus component noise count

Current semantic-scoring work in progress:

- `scripts/track_conversion_utils.py` now includes a first semantic reference builder and
  rendered semantic scorer.
- The scorer tracks per-region fill on the rendered crop:
  - `track_black_fill_1x`
  - `box_black_fill_1x`
  - `text_white_fill_1x`
  - `bg_white_fill_1x`
  - `accent_white_fill_1x`
- It also computes multi-scale semantic transfer, boundary IoU, hierarchy, and noise.
- This first implementation is useful for diagnostics, but it is not yet calibrated well
  enough to fully trust automated search runs; semantic reference extraction still needs
  tightening so good-looking candidates rank above obviously broken ones.

## Session Notes

- Iterative candidate sets were tested and compared primarily through endpoint
  render scoring.
- Candidate snapshots and rendered previews were written to `/tmp` during tuning.
- Going forward, all tuning scripts are committed first, then run.
- Layered parallel search (`10000` trials, `16` workers) produced a best verified
- Layered parallel search (`10000` trials, `16` workers) produced a best verified
  render score of `104.97` for `albert_park`, slightly above the previous default
  baseline (`104.55`).
- Follow-up local fine-tuning (`20000` trials around the saved winner) did not beat
  the broad winner; the best local result reached `104.93`, so the broad-search
  winner remains the applied result.
