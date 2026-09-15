# Display image pipeline

Calendar maps use the [reviewed open vector pipeline](TRACK_ARTWORK.md): 26 circuits,
Jules Roy and Wikimedia Commons sources, automatic fitting and rotation, 18 styles,
16 accent selections and four device palettes. The default is `relief / all / jules`.
Maps are drawn at the final panel size. There are no shipped legacy track BMPs.

| Preprocessing palette | API `display` | Flag output |
| --- | --- | --- |
| `mono` | `1bit` | `app/assets/flags_processed/` |
| `bwr` | `bwr` | `app/assets/flags_bwr/` |
| `bwry` | `bwry` | `app/assets/flags_bwry/` |
| `spectra6` | `spectra6` | `app/assets/flags_spectra6/` |

## Flags

Flat Flagpedia/Flagcdn PNG sources live in `app/assets/flags_flat/`. Their public-domain
source declaration is recorded in [DATA_LICENSES.md](DATA_LICENSES.md). Preprocessing
resizes them to 87×58. Monochrome flags use luminance-ranked patterns; color flags use
the same fixed palette and BMP encoder as the panel renderer.

```bash
uv sync --locked --group dev
uv run python -m scripts.manage preprocess flags --palette mono
uv run python -m scripts.manage preprocess flags --palette bwr
uv run python -m scripts.manage preprocess flags --palette bwry
uv run python -m scripts.manage preprocess flags --palette spectra6
```

Review generated changes and update individual hashes in the asset register only after
reviewing their source and licence. The checker never approves unknown files automatically.

## Maps and original brand artwork

```bash
uv run scripts/build_track_catalog.py --check
uv run scripts/check_track_assets.py
uv run scripts/build_brand_assets.py
uv run scripts/check_legal_assets.py
```

See [TRACK_ARTWORK.md](TRACK_ARTWORK.md) for the offline map rebuild, layout limitations,
licences, attribution and API options. Brand SVGs are original geometric drawings;
`build_brand_assets.py` creates the header, favicon family and social preview without
third-party image inputs. Team names and driver numbers use ordinary open fonts with
original simple markers.

## Retired workflow

The public `import track` and `preprocess tracks` commands are retired. The previous F1
raster/PSD sources and driver portraits are removed from the new working tree
and release packages. Legacy track wrappers fail with a usage error. Pure image-processing
helpers remain testable with synthetic, locally supplied inputs and require explicit source,
manifest and output locations; they do not grant permission to use an image or publish it.
There is no public upload or URL-to-image import endpoint.
Existing team logos and the project's AI car remain, with separate rights disclosures
in `DATA_LICENSES.md`; the open map licences do not cover those assets.

The static server requires an individually reviewed asset entry and matching SHA-256.
Stale installed files and old generated BMP/preview metadata are rejected. CI inventories
the actual wheel, source archive and Docker assets as well as the source tree.

Visual review covers 800×480 output in every palette, readable source credits, mobile
configuration and missing assets. Tests check palette limits, clipping across all map
styles, rotation, attribution, stale caches and blocked legacy distribution. Old screenshots
containing withdrawn artwork are not test fixtures. See [upgrade guidance](docs/data-collection.md#upgrading-to-130)
for cache migration and copies outside the current release.
