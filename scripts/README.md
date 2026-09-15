# Maintenance scripts

Run commands from the repository root using `uv sync --locked --group dev` and `uv run`.

| Command | Purpose |
| --- | --- |
| `uv run scripts/build_track_catalog.py` | Compile reviewed, archived open SVGs offline |
| `uv run scripts/build_track_catalog.py --check` | Verify the compiled catalogue and notices |
| `uv run scripts/check_track_assets.py` | Require Jules and Commons outlines for active circuits |
| `uv run scripts/generate_og_image.py` | Rebuild the project text-based social preview; preserves the original AI car and favicons |
| `uv run python -m scripts.manage preprocess flags --palette mono` | Rebuild flags; also supports `bwr`, `bwry`, `spectra6` |
| `uv run scripts/check_legal_assets.py` | Check every public file against its reviewed licence/hash record |
| `uv build --out-dir dist/legal-check` | Build actual release artifacts |
| `uv run scripts/check_legal_assets.py dist/legal-check/*` | Inspect wheel and source archive contents |
| `uv run scripts/check_public_deployment.py` | Report unresolved operator configuration before publication |
| `uv run scripts/update_seasons.py` | Refresh licensed Jolpica calendars and record provenance |
| `uv run scripts/update_historical.py` | Refresh sporting results through the app service |
| `uv run scripts/download_flags.py` | Acquire Flagcdn flags for subsequent source/licence review |

The register is a review record, not generated approval. Dataset updates intentionally fail
the asset check until the new bytes, provenance and licence have been reviewed. The seasonal
update workflow may prepare a PR; it must not silently accept new map sources or data hashes.

The F1 circuit scraper, wiki team scraper, team-logo and driver-photo downloaders have been
removed. Old track import/preprocessing commands are retired. Flag compatibility wrappers
still work. See [BMP_PROCESSING.md](../BMP_PROCESSING.md) and [TRACK_ARTWORK.md](../TRACK_ARTWORK.md).

`generate_og_image.py` directly renders the text-based Open Graph image with Pillow. Renderer benchmarks,
`material_diff.py`, alignment checks and panel test-image scripts remain local diagnostics.
Do not publish an old diagnostic image without reviewing the depicted assets.

`backup_cli.py` and `reset_db.sh` are installed as `backup` and `reset-db` in the container.
Follow [SELF-HOSTING.md](../SELF-HOSTING.md) and the
[upgrade guidance](../docs/data-collection.md#upgrading-to-130) for storage operations.
