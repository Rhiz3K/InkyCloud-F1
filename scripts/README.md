# Maintenance Scripts

Run Python maintenance commands from the repository root through the locked environment:

```bash
uv sync --locked --group dev
uv run python -m scripts.manage --help
```

## Asset preprocessing CLI

There is one supported preprocessing entry point:

```text
python -m scripts.manage preprocess tracks --palette {mono,bwr,bwry,spectra6}
python -m scripts.manage preprocess flags  --palette {mono,bwr,bwry,spectra6}
```

Track commands accept `--circuits monaco,suzuka`. Without it, all source stems under
`artwork/tracks/` are processed. See [`BMP_PROCESSING.md`](../BMP_PROCESSING.md) for source naming,
algorithms, output directories, and the weekly visual-review checklist.

The implementation lives in `app/services/asset_preprocessing.py`; palette variants are data,
not separate algorithms. These legacy names only forward arguments to the CLI during migration:

- `preprocess_tracks.py`, `preprocess_tracks_bwr.py`, `preprocess_tracks_bwry.py`,
  `preprocess_tracks_spectra6.py`
- `preprocess_flags.py`, `preprocess_flags_bwr.py`, `preprocess_flags_bwry.py`,
  `preprocess_flags_spectra6.py`

Do not add new palette-specific scripts.

## Data maintenance

| Command | Purpose |
| --- | --- |
| `uv run python scripts/update_seasons.py` | Fetch and validate season calendars; exits non-zero for malformed/empty upstream data |
| `uv run python scripts/update_historical.py` | Thin wrapper for historical-result refresh logic shipped in `app/` |
| `uv run python scripts/scrape_circuits.py` | Refresh circuit metadata without discarding maintained history |
| `uv run python scripts/scrape_wiki_teams.py` | Maintain team metadata from its source |

Season updates normally run through `.github/workflows/update-f1-data.yml`, weekly in-season and
daily during December–February. The workflow opens a pull request when tracked data changes.

## Asset acquisition

| Script | Output |
| --- | --- |
| `download_flags.py` | validated flat flag PNG sources |
| `download_driver_photos.py` | driver image sources |
| `download_team_logos.py` | team logo sources |
| `generate_og_image.py` | social preview image |

Downloaders validate payloads before atomically replacing an existing asset. Review licensing and
source provenance before committing downloaded files.

## Diagnostics and visual review

| Script | Purpose |
| --- | --- |
| `material_diff.py` | compare rendered output bytes/pixels across changes |
| `benchmark_renderer.py` | measure renderer and optional HTTP performance |
| `measure_alignment.py` | inspect layout alignment |
| `generate_test_image.py` | create a representative panel test image |
| `generate_stress_test_image.py` | exercise difficult panel patterns |
| `find_longest_values.py` | find layout-stressing source values |

Diagnostics may create local output files; inspect `git status` before committing.

## Container operations

`backup_cli.py` and `reset_db.sh` are copied into the production image as `backup` and `reset-db`.
Use the documented container commands in [`SELF-HOSTING.md`](../SELF-HOSTING.md) rather than
invoking their repository paths in production.
