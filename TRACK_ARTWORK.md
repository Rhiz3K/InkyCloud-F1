# Open circuit artwork

Calendars render from 26 reviewed Jules Roy outlines and 26 individually reviewed
Wikimedia Commons alternatives. Defaults: `relief` / `all` / `jules`. A new calendar
configuration selects Spectra 6 for all four chromatic accents. Existing `/calendar.bmp`
URLs retain their one-bit hardware default; use `display=spectra6` for all six colours.

The extrusion is decorative, not elevation. Colour divisions are not official sectors.
Each circuit uses the listed layout, including when selecting an older race. Historical
rebuilds are not inferred. Las Vegas Commons uses a selected outer outline, identified
as `outline-subpath` in its metadata.

## API and choices

`GET /api/tracks` lists 18 styles, 16 accent subsets, two source families, four display
palettes and circuit IDs. `GET /api/tracks/{circuit_id}` exposes authorship, licences,
source checksums, modifications and upstream attribution. The calendar and
`/preview/configure/calendar.png` accept:

| Parameter | Default | Values |
| --- | --- | --- |
| `track_style` | `relief` | All 18 identifiers from the catalogue |
| `track_source` | `jules` | `jules`, `commons` |
| `track_accent` | `all` | All 16 subsets from the catalogue |
| `display` | `1bit` | `1bit`, `bwr`, `bwry`, `spectra6` |

Unavailable accents are omitted, never substituted. An empty intersection is
monochrome. Invalid artwork choices return HTTP 422; unknown standalone circuits
return 404. Unknown circuits in calendars use a neutral placeholder without a legacy
F1 raster fallback.

```text
/calendar.bmp?display=spectra6&track_style=relief&track_source=jules&track_accent=all
/calendar.bmp?display=bwry&track_style=mosaic&track_source=commons&track_accent=warm
/preview/configure/calendar.png?display=spectra6&track_style=contours&track_source=commons
/api/tracks/madring.svg?track_style=relief&track_source=jules&track_accent=all
/api/tracks/monza.png?track_style=contours&track_source=commons&display=bwr
/api/tracks/suzuka.bmp?track_style=pixel&track_accent=cool&display=spectra6
```

Standalone SVG/PNG/BMP default to Spectra 6 and fit within 494 × 271 pixels including
visible attribution. PNG retains full attribution as iTXt; SVG has metadata and a
clickable credit link. Image endpoints link to per-file `/credits`; standalone artwork
also has an HTTP `rel=license` link. The map licence does not relicense other material
in a complete calendar, the race database, or the application code.

Calendar BMPs and their previews omit the visible credit row below the track. The
map uses the full available height. Per-file authorship and licences remain available
in Credits and through the image response's HTTP links; standalone track exports
retain their visible attribution and SVG/PNG metadata.

The statistics dashboard breaks calendar requests down by `track_style`,
`track_source` and requested `track_accent` for the selected time range. The last
24 hours are also available in `/api/stats` under `requests.by_track_style`,
`requests.by_track_source` and `requests.by_track_accent`. Counts include cached
responses and HTTP 304s. Schema migration 6 labels unrecorded historic choices
`legacy`; it does not infer past choices from today's defaults. New requests record
resolved defaults even when the URL omits them. The requested accent is counted
before intersection with the display's supported colours.

## Reproducible sources

`artwork/open-tracks/catalog-source.json` contains reviewed selections, with archived
original SVGs and manifests beside it. Jules Roy is pinned to commit
`9c93759b076d1b87eac265a009b21b399253220a` under CC BY 4.0. Commons contains 14 CC0,
nine CC BY-SA 4.0 and three CC BY-SA 3.0 outlines. Marina Bay retains Cherkash and
the underlying Sentoan attribution. Our adaptation of CC0 outlines is offered under
CC BY 4.0; ShareAlike artwork retains its required licence.

```bash
uv run scripts/build_track_catalog.py
uv run scripts/build_track_catalog.py --check
```

The offline builder checks catalogue coverage, original SHA-256 hashes and reviewed
attribution before compiling absolute curve commands. Adding a circuit requires
explicit layout and licence review, archived SVG, manifest/selection updates and
visual inspection. It does not download or automatically approve sources. The public
API accepts neither uploaded SVGs nor arbitrary remote images.

Bezier commands remain exact; arcs become cubic spans of at most 7.5 degrees. Runtime
fitting flattens curves with a conservative control-hull tolerance of 1/20,000 of the
source span plus numerical reserve. The original curve is drawn. Rotation enumerates
convex-hull events, stationary extents and constraint crossings to maximise uniform
scale. Relief is projected before rotation. Native-pixel margins include outer
strokes, dividers, displaced ink and extrusion.

`resvg-py` renders local SVG without a browser or system fonts. Pillow restricts pixels
to the effective palette without dithering. Geometry and immutable PNG caches are
bounded. Calendar cache identities include the drawing version, catalogue digest and
all artwork options. Scheduler pre-generation covers default artwork only; custom
choices render on the existing worker pool. Old generated calendar metadata is rejected.
Increment `ARTWORK_VERSION` when changing drawing/fit/palette behaviour; source changes
invalidate it automatically.

Tests rasterise outside the viewport for all 936 circuit/source/style combinations,
compare fitting to an independent angle oracle and verify all 1,152
style/accent/hardware combinations. They also cover API validation, attribution,
ETags and independent calendar/preview selections.

Legacy track rasters, PSDs and driver portraits have been removed from the new release.
Existing team logos and the project's AI car artwork remain; their separate rights
status is documented in `DATA_LICENSES.md` and is not covered by the map licences.
The old public import/preprocessing commands are retired, static serving is restricted
by a reviewed asset register, and old calendar/teams cache metadata is invalidated.
See [upgrade guidance](docs/data-collection.md#upgrading-to-130) for cache migration
and copies outside the current release.
