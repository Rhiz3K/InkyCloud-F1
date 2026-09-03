# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.40] - 2026-09-02

### Backend

#### Fixed

- **Season calendar automation** - Treat an unpublished next season as "nothing to update" instead of a failure, so the weekly Jolpica sync no longer aborts before its pull request step; the workflow now still opens a PR for seasons that did update and only then reports a failed year, while a separate monitor opens one assigned issue for failures and closes it after recovery
- **Startup readiness order** - Generate calendar images before the historical catch-up and the all-circuit weather fetch, so a slow or rate-limited Jolpica no longer keeps `/health/ready` in `starting` past the container health-check window; scheduler-disabled deployments fetch weather before their only generation instead
- **Operational API hardening** - Apply the per-IP rate limit before token validation on `/api/stats`, `/api/stats/history`, and `GET /api/perf-metrics`, and compare tokens as bytes so a non-ASCII header returns 401 instead of 500
- **Bounded upstream fan-out** - Cache only validated Jolpica season and round payloads (one hour, one minute for empty answers and failures) with per-key coalescing, retry malformed non-empty payloads, and keep degraded team rosters for five minutes, so request bursts for uncached seasons cannot queue hundreds of paced upstream calls behind the scheduler without poisoning the cache
- **Changelog page resilience** - Remember a failed uncached GitHub version lookup for five minutes instead of repeating two blocking API calls on every `/changelog` view
- **Perf metrics input** - Validate `page_path` as a site-relative path, collapse unknown pages into `/other`, and lower the default per-IP beacon quota to 30 per minute so unauthenticated clients cannot inflate the dashboard or the database
- **Static season reads** - Parse each bundled season file once per on-disk version and log per-request lookups at debug level instead of re-reading JSON and logging at info on every calendar request

#### Changed

- **2026 calendar snapshot** - Add the replacement Sepang round on 4 October, renumber the following rounds, retain the cancelled Bahrain and Jeddah entries, and adopt Jolpica's corrected Miami first-practice time
- **reset-db safety** - The maintenance script now runs with strict shell mode, uses the application's SQLite busy timeout, refuses `all` while WAL/SHM sidecars show an open connection unless `--force` is passed, and documents running it against a stopped service

### Frontend

#### Fixed

- **Translation strings in configure markup** - Escape localized labels before they are injected via `innerHTML`, closing a stored-XSS path through translation files
- **Stylesheet loading** - Load the primary stylesheets as render-blocking links with versioned URLs, removing the unstyled first paint and layout shift caused by the preload swap and the stale one-hour static cache after deploys
- **Accessibility** - Name the icon-only sidebar close button and associate the timezone, race, and season controls with their labels
- **Track maps on 1-bit displays** - Resample preprocessed monochrome maps in grayscale and re-threshold, because Pillow silently uses nearest-neighbour for 1-bit images and broke thin track lines into dashes
- **Render performance** - Count logo activity rows in Pillow's C loops and pack 4-bit BMP nibbles without a per-pixel Python loop, cutting the startup warmup from about nine seconds to well under one and each B/W/R render by roughly a hundred milliseconds

### Security

#### Changed

- **Container hardening** - Drop all Linux capabilities and disallow privilege escalation in Compose, run uvicorn as PID 1 via `exec`, tag Sentry events with the release, and HTML-escape the Umami website id in the tracking tag
- **CI maintenance** - Let Dependabot update the composite actions, align the `setup-uv` pin, remove an unused OIDC permission, and document the uv-based development setup

## [1.2.39] - 2026-08-24

### Frontend

#### Added

- **Monza track assets** - Add the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Monza display support

## [1.2.38] - 2026-08-21

### Security

#### Fixed

- **Fresh container security updates** - Upgrade Debian runtime packages during image builds and bypass the runtime-stage cache in security and release workflows so available OS fixes are applied before scanning or publishing

## [1.2.37] - 2026-08-04

### Backend

#### Added

- **Season rollover guard** - Fail the test suite once `SEASON_START_DATES` no longer covers the current UTC year so a missing first-race date cannot silently reintroduce empty-season Jolpica probes

#### Changed

- **Cross-service Jolpica pacing** - Share one event-loop-safe token bucket per upstream hostname, allowing a six-request teams cache-miss burst while keeping sustained historical, F1 fallback, standings, teams, and retry traffic paced
- **Bounded historical refreshes** - Cap the nightly refresh at a configurable 90-minute wall-clock budget, persist completed circuit updates before cancellation, count timeouts as one incomplete run, and log elapsed duration for every execution

## [1.2.36] - 2026-08-03

### Backend

#### Fixed

- **Rate-limit-safe historical refreshes** - Pace every Jolpica request and retry, honor a patient configurable retry budget, skip immutable current-season circuit results, stop season fallbacks after transient upstream failures, advance freshness after transient-only runs, and keep incomplete-run warnings grouped with structured circuit context
- **Escalating alerts for persistently incomplete refreshes** - Track consecutive incomplete runs so a recurring problem raises one grouped error after three runs instead of disappearing behind per-run warnings, and log failed circuit names to stdout where the log format cannot render structured fields
- **Forced single-circuit refreshes** - `--circuit <id>` now always fetches instead of silently skipping a circuit that already holds current-season results
- **Deterministic CLI shutdown** - `scripts/update_historical.py` releases the shared outbound HTTP client it borrows rather than leaving the connection pool open when the loop closes

#### Changed

- **Wider transient-failure retries** - `fetch_with_retry` now treats every `5xx` as retryable instead of only `502/503/504`, which also affects the F1, weather, standings, and teams services

## [1.2.35] - 2026-07-26

### Frontend

#### Added

- **Zandvoort track assets** - Add the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Circuit Zandvoort display support

## [1.2.34] - 2026-07-21

### Backend

#### Added

- **Conditional BMP delivery** - Return strong SHA-256 ETags for calendar, teams, pregenerated, cached, fresh, and error BMPs; honor `If-None-Match` with an empty 304 response after revalidating the pregenerated sidecar without reading its BMP body; and retain status-specific request statistics without counting 304s as analytics downloads
- **Dependency-aware readiness** - Add `/health/ready` checks for asynchronous SQLite access, writable persistent storage, the `/app/data` mount root, and recent core calendar output using the same six-hour tolerance as pregenerated serving; expose optional generation failures as a healthy `degraded` state and retain a five-minute Docker cold-start grace

#### Changed

- **Unified renderer architecture** - Introduce immutable render themes, central display enumerations, shared adapters and palette quantization, concern-specific renderer modules, and centralized font loading while preserving byte-identical calendar and teams output for all four displays
- **Generation and persistence efficiency** - Run scheduled render batches through the bounded two-worker pool, coalesce identical cold BMP requests with cancellation-safe singleflight, share one application-scoped database connection, and split scheduler generation, weather, and maintenance concerns
- **Runtime asset packaging** - Move editable track PNG/PSD sources to top-level `artwork/`, narrow wheel and Docker inputs to processed runtime assets, and reduce each artifact by more than 100 MB

#### Fixed

- **Backup cron correctness** - Normalize Sunday-first, wrapped, ranged, and stepped standard-cron day-of-week expressions correctly and fail startup for invalid enabled backup schedules instead of silently losing backups
- **Deterministic font layout** - Pin Pillow to its universally available BASIC layout engine so optional host RAQM libraries cannot change calendar or teams BMP pixels from the slim production-image baseline
- **Generation health accuracy** - Stamp freshness after any run that publishes core calendar artifacts, report weather or secondary-variant failures as degraded without causing restart loops, keep previous files during partial runs, and derive all generated filenames from shared image-key helpers
- **Season calendar automation** - Run the validated season updater weekly in-season and daily during December through February using the locked environment, with malformed or empty upstream data failing visibly
- **Track artwork preservation** - Regenerate all four shipped runtime palettes from the maintained source artwork, restore normalized Las Vegas filenames, and fail tests when processed BMPs drift from their sources

### Security

#### Added

- **Continuous vulnerability scanning** - Audit the exported production lock with `pip-audit` and scan the built container with Trivy for high and critical vulnerabilities on pull requests and a weekly schedule
- **Attested release containers** - Publish version, short-commit, and latest GHCR tags from release tags with least-privilege permissions, GHA layer caching, an SBOM, and provenance

#### Fixed

- **Patched image dependency** - Upgrade Pillow to 12.3.0 after the new audit identified known vulnerabilities in 12.2.0, with renderer golden hashes confirming unchanged output
- **Coordinate log minimization** - Keep weather-fetch diagnostics while omitting latitude and longitude values from application logs

### Development

#### Changed

- **Python 3.14 everywhere** - Align package metadata, Ruff, mypy, CI, benchmarks, data maintenance, local setup, and both Docker stages on the production interpreter
- **One dependency workflow** - Make the `uv` dependency group and `uv.lock` canonical, generate the hash-locked Docker requirements export, fail CI on export drift, and remove the duplicate optional-development list and ad-hoc installs
- **Shared CI implementation** - Route trusted, fork, and CodSpeed jobs through reusable locked setup/composite actions while preserving the self-hosted fork boundary; replace the hard 100% global gate with 95% statement-and-branch coverage plus a 100% changed-line ratchet
- **Behavior-oriented tests** - Merge or rename every coverage-oriented `_extended` module, add renderer and preprocessing byte-golden tests, and cover Sunday cron ranges, singleflight, ETag/304, shared database lifecycle, and readiness failure modes
- **Unified asset CLI** - Replace eight duplicated palette preprocessors with `python -m scripts.manage preprocess`, keep thin compatibility wrappers directly executable by filename, and parameterize the application-owned implementation from renderer palette constants

### Documentation

#### Changed

- **Single-source deployment guidance** - Make `.env.example` the configuration reference, reduce duplicated deployment prose to focused Docker and Coolify guides, document immutable GHCR upgrades/rollback and readiness, and add dedicated scripts and BMP workflow references
- **ESP32 revalidation** - Document persistent ETag handling so a 304 response skips both transfer and disruptive E-Ink redraw

## [1.2.33] - 2026-07-19

### Frontend

#### Added

- **Hungaroring track assets** - Add the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Hungaroring display support

### Development

#### Fixed

- **Production scheduler packaging check** - Import the historical refresh binding from the final Docker image outside the application working directory so CI catches dependencies on repository-only scripts

## [1.2.32] - 2026-07-11

### Backend

#### Fixed

- **Full-year statistics retention** - Retain 400 days of statistics by default so the `365D` dashboard remains complete while database and backup growth stay bounded; deployments explicitly configured for 90 days must switch to `400` or `0` and redeploy

### Development

#### Fixed

- **Post-release CI validation** - Isolate the release-validation entrypoint test from repository tags so the tagged release commit passes `main` CI without weakening the pull-request version-bump gate

## [1.2.31] - 2026-07-09

### Security

#### Fixed

- **Configuration and request hardening** - Reject empty admin tokens and storage paths, keep display dimensions fixed, select static season and roster files from internal allowlists, validate backup cron expressions without crashing startup, use fixed-window rate limiting, and add baseline browser security headers
- **HTML and redirect defense-in-depth** - Cover all relevant URI-bearing changelog attributes, remove inline style vectors, reject backslash redirect paths, and build API examples from the configured public URL instead of the request Host header
- **Resource protection** - Bound decoded-image memory, rate-limit live F1 and statistics reads, constrain operational query windows and payload fields, preserve `429` responses, and trust forwarded client addresses only from loopback by default
- **Supply-chain reproducibility** - Pin GitHub Actions and the Python Docker base image to reviewed immutable digests, install exact locked runtime dependencies in Docker, disable persisted checkout credentials, scope workflow permissions to individual jobs, serialize release/data/benchmark runs, and keep major and Docker Dependabot updates behind human review

### Backend

#### Fixed

- **Production historical refresh** - Move the daily refresh into the shipped application package, reject incomplete Jolpica podium rows, write updates atomically, persist refresh age, and catch up a missed daily run at startup
- **Scheduler and persistence resilience** - Requeue statistics on every database failure path, isolate shutdown cleanup, prevent overlapping weather/history jobs, validate persisted weather fields and age, recheck negative caches under fetch locks, and keep previously-good images during degraded runs
- **F1 data correctness** - Preserve complete cancelled-race fields, cache parsed static-season snapshots by content, skip only malformed historical rows, use current driver numbers, and keep standings/team mappings aligned with the latest constructor data
- **API and image reliability** - Retry transient transport/5xx failures, avoid pregenerated-image read races, return controlled render errors, validate timezones consistently, and use static data plus rate limits for public race endpoints
- **Persistent F1 data** - Store mutable circuit history beside the SQLite database, reload atomic file versions without restarting, preserve maintained history during scraping, and mark daily refreshes complete only after a successful upstream run
- **Upstream resilience** - Share the configured Jolpica base URL, coalesce standings fetches, cache bounded misses, honor `Retry-After` with jitter, and serve the latest completed race during an empty off-season calendar
- **Rendering correctness and efficiency** - Select active team drivers by round, enforce the fixed 800x480 canvas, use a 64 MiB decoded-asset cache, accelerate BWR/BWRY palette mapping, reuse cached fallback tracks, and pluralize countdown units per locale including French and Brazilian Portuguese zero forms
- **Storage durability** - Fsync atomic files and parent directories with unsupported-filesystem fallbacks, serialize cancellation-safe schema setup, close partially configured SQLite connections, cap statistics history and performance samples, report percentile sample sizes, and remove reset sidecars safely

#### Changed

- **Image generation performance** - Build preview PNGs from already-rendered BMPs, cache track and F1-logo decoding, share driver-photo caches, remove redundant font loads, and warm both render workers before serving traffic
- **Asset preprocessing** - Use shared palette mapping and atomic output for all preprocessors, normalize circuit IDs, and add the missing Spectra 6 flag generator
- **Installable resources** - Resolve templates, assets, translations, and changelog data from stable package paths; wheels now work outside the source checkout and Docker dependency layers no longer reinstall for every source edit

### Frontend

#### Fixed

- **Dynamic configuration** - Derive Teams seasons from backend data, restore localized mobile navigation and missing-changelog messages, redact public render failures, avoid caching transient error images, and revalidate every static asset in the service worker
- **Cold-start calendar preview** - Render the next-race calendar on demand while background pregeneration is still running, shorten preview caching, and fall back to the live calendar BMP instead of the generic social banner
- **Web Vitals delivery** - Send Beacon API payloads with the JSON media type expected by FastAPI and retry with `fetch` when the browser cannot queue a beacon

### Development

#### Changed

- **Release and CI consistency** - Align Python, frontend, runtime, and API version metadata at 1.2.31, keep Docker on Python 3.13, and run mypy for production code in both CI paths
- **Dependency automation resilience** - Keep ordinary Dependabot updates eligible for auto-merge without a redundant alert-metadata fetch and exclude Python Docker base-image minor upgrades from generic semver auto-merge
- **Hermetic verification** - Add `httpx2` to development dependencies, remove live API calls from endpoint tests, exercise real BMP render pipelines and persistence failure paths, and make all asset update scripts fail on partial errors
- **Documentation enforcement** - Document every Python module, class, method, and function in `app` and `scripts`, and enforce 100% docstring coverage in both CI paths
- **Test coverage enforcement** - Cover every application statement and branch, require 100% coverage through the shared local configuration, and enforce the same strict gate in both trusted and fork pull-request CI paths
- **Maintenance cleanup** - Remove unused standings rendering and personal debug scripts, centralize image conversion and filename rules, validate downloaded images before atomic replacement, and align deployment guidance with the single-writer SQLite architecture

## [1.2.30] - 2026-07-06

### Frontend

#### Added

- **Spa-Francorchamps track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Circuit de Spa-Francorchamps display support

#### Changed

- **Tailwind tooling patch** - Updated the Tailwind CLI lockfile to 4.3.2 and regenerated the checked-in minified Tailwind asset with the patched toolchain

### Backend

#### Fixed

- **F1 result ordering** - Fetch full Jolpica qualifying and race result payloads and sort them by numeric position before storing top-three finishers, fixing completed races that showed paginated subsets instead of the actual podium/order
- **Bundled historical results** - Refreshed the bundled completed-race data with corrected 2025 and 2026 qualifying/race results so production displays no longer depend on stale generated snapshots
- **Historical refresh consistency** - Historical result refreshes now ignore malformed/non-numeric result positions, write `circuits_data.json` atomically, and serialize updates with image generation so readers never see partial result data

#### Changed

- **Daily historical result refresh** - The application now refreshes completed-race results once per day and regenerates display data when results change, replacing scheduled PR-based historical result updates

### Development

#### Changed

- **Dependabot auto-merge** - Safe Dependabot patch and minor PRs now enable squash auto-merge after required checks, with security PRs also covered when `DEPENDABOT_METADATA_TOKEN` is configured for Dependabot alert metadata; release-gate CI checks skip Dependabot PRs by PR author and the metadata action is pinned to a reviewed commit
- **F1 data workflow scope** - Kept manual season-calendar refresh support while removing the scheduled historical-data PR path now handled by the running application

## [1.2.29] - 2026-06-29

### Frontend

#### Added

- **Silverstone track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Silverstone Circuit display support

## [1.2.28] - 2026-06-14

### Frontend

#### Added

- **Red Bull Ring track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Red Bull Ring display support

## [1.2.27] - 2026-06-09

### Security

#### Fixed

- **Starlette host-header advisory (CVE-2026-48710)** - Upgraded locked `starlette` to 1.2.1 to resolve the Dependabot alert for missing `Host` header validation that could poison `request.url.path`
- **CI no longer runs untrusted fork PRs on the self-hosted runner** - Gated the `test` job so pull requests from forks don't execute checked-out code on the persistent self-hosted runner that shares secrets with the release workflow; fork PRs run the same validation on an ephemeral GitHub-hosted runner instead
- **Rate-limit client identification** - Per-IP rate limiting now uses the proxy-validated client address instead of the spoofable leftmost `X-Forwarded-For` entry, so a client can no longer forge a fresh bucket on every request
- **Changelog HTML sanitization** - Rendered changelog markdown is sanitized (script/style/event-handler/`javascript:` removal) as defense-in-depth before being emitted
- **Configure page output escaping** - Upstream team/driver/country strings injected into the configure DOM are now HTML-escaped, and race-list round/race-key values are coerced to numeric/slug-safe forms before reaching attribute and inline-JS contexts

### Backend

#### Fixed

- **Atomic image generation** - Hourly generation writes all variants before pruning stale files and isolates each render, so a single failed variant can no longer delete the entire pregenerated set for an hour
- **Renderers no longer block the event loop** - Calendar/teams BMP rendering and PNG conversion (including hourly preview generation) run on a small dedicated render executor, with renderers constructed in the worker thread so font objects stay thread-local
- **Thread-safe renderer asset caches** - Guarded the shared team-logo and driver-photo caches (both the Spectra 6 and 1-bit hierarchies) with a lock now that rendering runs in a thread pool, so concurrent first-loads can't race into redundant disk reads
- **Atomic pregenerated file writes** - BMPs and preview PNGs are written to a temp file and atomically renamed, so a request landing mid-rewrite can no longer read (and cache) a truncated image
- **Pregenerated freshness gate** - Pregenerated calendar/teams files are served only while less than 6 hours old, so a variant that stops regenerating (dropped timezone popularity, persistent render failure) falls back to on-demand rendering instead of showing the previous race indefinitely
- **Stale prune protects weather variants** - The hourly prune is skipped when weather is enabled but no live weather came back, so an open-meteo outage no longer deletes every previously-good weather-variant BMP
- **Black/White/Red results flags** - Restored the country-to-flag map for the BWR renderer, fixing the Austrian GP rendering the Australian flag and several Grands Prix rendering no flag
- **Sauber logo on BWR/BWRY teams screens** - Keyed the team-logo cache per renderer class so the palette-specific Sauber logo preparation runs instead of inheriting the Spectra 6 variant
- **Scheduler misfire handling** - Background jobs use a 5-minute misfire grace, so a briefly-busy event loop no longer silently skips hourly generation or the daily backup
- **Popular-timezone pregeneration is served** - Calendar requests for popular non-default timezones now serve the pregenerated BMP instead of always rendering on demand
- **Teams dashboard resilience** - A standings-fetch failure serves the bundled team data without caching it, so the next request retries standings immediately instead of pinning a degraded dashboard for the cache TTL
- **Weather accuracy** - Precipitation probability is read from the current hour instead of midnight, and missing or null API fields return no data instead of fabricating 20°C/sunny weather on both the current and race-day paths
- **Next-race boundary** - The calendar keeps showing the current race for a grace window after lights-out instead of flipping to the next Grand Prix the moment it starts
- **Statistics durability** - The in-memory API-call buffer is bounded structurally (deque), re-queued on a failed or cancelled flush, and flushed on shutdown after scheduler job cancellations settle
- **Version info resilience** - A failed GitHub refresh keeps the last known version instead of caching an empty result for the full hourly window
- **Weather restart persistence** - Prefetched next-race weather is restored from the database on startup under UTC-normalized cache keys shared by all save/read sites, stamped with its original fetch time so true data age drives expiry

#### Changed

- **Statistics retention documentation** - Clarified that `STATS_RETENTION_DAYS` keeps all history by default (0) and pruning is opt-in to bound statistics table and backup growth
- **Version info caching** - The changelog page serves cached GitHub version info for the full hourly refresh window instead of blocking on an inline fetch when the short cache expired
- **uvicorn minimum version** - Raised to 0.30 for CIDR support in `--forwarded-allow-ips`

### Frontend

#### Fixed

- **Configure preview timezone** - The pre-rendered preview is used only when the selected timezone matches the server default, so non-default-timezone visitors see an accurate preview
- **Service worker asset freshness** - CSS/JS use a stale-while-revalidate strategy (with the cache write covered by the worker lifetime, always resolving to a valid response) so updates reach returning visitors without a manual cache-version bump; immutable fonts/images stay cache-first

### Development

#### Changed

- **Operational API documentation** - Clarified that the stats and perf-metrics read endpoints are public by design unless `ADMIN_API_TOKEN` is configured
- **Docker Compose port binding** - Documented narrowing the published port to loopback when fronting the app with a reverse proxy (the default stays LAN-reachable for directly-polling e-ink devices)
- **Single renderer factory and image-key source** - The display-to-renderer mapping and pregenerated-filename derivation each live in one shared helper instead of four/two diverging copies

## [1.2.26] - 2026-06-08

### Frontend

#### Added

- **Catalunya track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Circuit de Barcelona-Catalunya display support

### Backend

#### Fixed

- **Sitemap lastmod accuracy** - Removed synthetic daily `lastmod` values from `sitemap.xml` so sitemap metadata no longer claims every localized page changed on every request

### Documentation

#### Changed

- **README project status refresh** - Updated public route, API, SEO, Docker quick-start, `race_key`, and operational API token documentation to match the current app behavior

## [1.2.25] - 2026-05-25

### Frontend

#### Added

- **Monaco track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Circuit de Monaco display support

## [1.2.24] - 2026-05-25

### Frontend

#### Fixed

- **Spectra 6 schedule contrast** - Added a subtle black shadow behind colored weekend schedule session labels so yellow qualifying and sprint qualifying text remains legible on white backgrounds
- **IST timezone autodetection** - Normalize the legacy browser timezone alias `Asia/Calcutta` to `Asia/Kolkata` in the configure flow so generated calendar URLs use the canonical IANA timezone

### Backend

#### Fixed

- **IST timezone aliases** - Accept and canonicalize `Asia/Calcutta` as `Asia/Kolkata` in calendar BMP requests and timezone conversion helpers so legacy links still render instead of returning an invalid-timezone error

### Security

#### Fixed

- **Dependabot Python security patches** - Updated locked `urllib3` to 2.7.0 and `idna` to 3.16 to address the open Dependabot alerts for urllib3 redirect/header handling, urllib3 streaming decompression, and IDNA input processing

### Development

#### Changed

- **Tailwind tooling update** - Updated `tailwindcss` and `@tailwindcss/cli` to 4.3.0 so the checked-in Tailwind asset build stays current with the npm minor-and-patch dependency group

## [1.2.23] - 2026-05-04

### Frontend

#### Added

- **Villeneuve track assets** - Added the source PSD and B/W, B/W/R, B/W/R/Y, and Spectra 6 rendered track images for Circuit Gilles Villeneuve display support

## [1.2.22] - 2026-04-30

### Backend

#### Fixed

- **Jolpica standings URL handling** - Derived standings URLs from the configured next-race endpoint instead of appending season standings paths to `current/next.json`, and downgraded missing standings 404s to warnings so expected empty API responses no longer flood GlitchTip
- **Search Console sitemap compatibility** - Rebuilt `sitemap.xml` generation with escaped XML values, canonical localized page URLs including the public stats dashboard, hreflang alternates, and a trailing-slash fetch alias so Google can classify and crawl the submitted sitemap reliably

### Security

#### Fixed

- **PostCSS security patch** - Refreshed the Tailwind toolchain lockfile so the vulnerable PostCSS dependency path reported by Dependabot is no longer present
- **Tailwind dependency update** - Upgraded Tailwind CSS to 4.2.4 and added the separate Tailwind CLI package so the asset build keeps working while closing the Dependabot npm update

### Frontend

#### Changed

- **Tailwind 4 CSS build** - Switched the Tailwind input stylesheet to the v4 import flow and explicitly load the existing JS config so custom racing colors, Space Mono, and neo shadows remain generated

## [1.2.21] - 2026-04-29

### Backend

#### Fixed

- **F1 data refresh noise** - Stopped scheduled F1 data updates from opening pull requests when Jolpica refreshes only `generated_at` or `updated_at` metadata, while keeping real historical result, season-calendar, and future payload-field changes detectable through a shared exclusion-based material-diff helper

### Security

#### Fixed

- **Dependabot security patches** - Raised the locked and declared minimum versions for Pillow, lxml, python-dotenv, and pytest to patched releases reported by Dependabot security alerts

### Development

#### Changed

- **Dependabot security grouping** - Added explicit grouped security-update rules for Python and Tailwind-scoped npm dependencies, and added npm to the weekly Dependabot coverage for Tailwind asset tooling

## [1.2.20] - 2026-04-03

### Frontend

#### Fixed

- **Weather tooltip hover/click parity** - Kept the disabled `Race day` weather hint visible when desktop users click after hovering while preserving tap-to-toggle dismissal for touch users, so the floating tooltip no longer disappears until the pointer leaves and re-enters the trigger

### Backend

#### Fixed

- **SQLite backup snapshots** - Replaced byte-for-byte SQLite file copies with SQLite's online backup API for both scheduled and detailed S3 backups so WAL-enabled databases upload crash-safe, internally consistent snapshots
- **Translation and season fallbacks** - Stopped recursive i18n fallback loops when the default locale file is missing and made season helpers fall back to the current year beyond the last hardcoded race-start date while still preferring the newest bundled teams dataset
- **Config test cache reset** - Refreshed the module-level `config` singleton whenever tests clear the config cache so patched environment variables propagate consistently across imports
- **Static-analysis hygiene cleanup** - Reworked the config test-reset helper, analytics/standings/version logging calls, database cursor context-manager nesting, schema-init state naming, stats cleanup query whitelisting, and ASGI middleware constructor names so the branch stays clean under DeepSource Python review without changing runtime behavior
- **Background task supervision** - Replaced fire-and-forget `create_task()` call sites for startup generation and web-vitals analytics with supervised background tasks that keep references, log failures, and report exceptions to Sentry instead of dropping them silently
- **Configurable stats retention** - Extended the scheduled stats cleanup path to cover `api_calls` and `perf_metrics` alongside legacy `request_stats`, but now gate it behind `STATS_RETENTION_DAYS` so deployments keep statistics indefinitely by default and can opt into bounded cleanup only when explicitly configured
- **Hourly stats history source-of-truth** - Switched `/api/stats/history` to derive hourly and rolling-24h counts directly from persisted `api_calls` so historical stats work against existing production data even when the legacy `request_stats` snapshot table is empty, and rolling windows now stay correct across sparse hourly gaps
- **Cross-instance DB initialization safety** - Serialized schema initialization and migrations per database path instead of per `Database()` instance so concurrent request-scoped database handles cannot race each other into duplicate `ALTER TABLE` migrations on older SQLite files
- **ASGI middleware migration** - Replaced `BaseHTTPMiddleware` wrappers for static cache headers and canonical-host/HSTS handling with pure ASGI middleware so streaming responses keep middleware compatibility without sacrificing the existing redirect and cache behavior
- **Shared outbound HTTP clients** - Reused long-lived `httpx.AsyncClient` instances for Jolpica, Open-Meteo, GitHub release checks, and Umami requests, then closed them during app shutdown so outbound API calls stop paying per-request client setup costs without leaking connections across tests or process exit
- **Shared HTTP retry helper** - Consolidated the duplicated Jolpica 429 backoff logic into `app.utils.http.fetch_with_retry()` so F1, standings, and teams services share one tested retry path instead of maintaining three drift-prone copies
- **Config-driven standings endpoints** - Switched standings fetches to derive their Jolpica base URL from `config.JOLPICA_API_URL` and use UTC-aware default-year resolution so mirrored endpoints and year-rollover behavior stay consistent with the rest of the services layer
- **BMP cache headroom** - Increased the in-memory BMP cache capacity to 512 entries so localized display and weather combinations fit without immediate LRU eviction churn during normal traffic
- **Non-blocking BMP analytics** - Moved calendar BMP analytics dispatch in the image routes onto supervised background tasks so download responses no longer wait on Umami network round-trips
- **Configure redirect validation** - Validate configure screen types before localized redirect canonicalization so invalid `/configure/...` language-query permutations return a 404 instead of bubbling a `KeyError` into a 500
- **Query-safe redirect merging** - Hardened `_redirect_path()` to merge existing canonical query strings with preserved request parameters instead of producing malformed double-`?` redirects if future targets include their own query string
- **Method-preserving canonical host redirects** - Switched the `www` to apex redirect middleware to HTTP 308 so canonical redirects keep POST request bodies intact for endpoints like `/api/perf-metrics`
- **Static error cache headers** - Limited static-asset cache headers to successful `/static/...` responses so missing files keep their default `no-store` semantics instead of inheriting public asset caching
- **Startup task shutdown ordering** - Cancel pending initial image generation before closing shared HTTP clients and database handles so shutdown cannot race a still-running startup task against torn-down resources
- **Scheduler shutdown drain clarity** - Made scheduler shutdown explicitly wait for in-flight jobs after cancelling the startup generation task so shared HTTP/database resources are only closed once background work has drained
- **Umami warning deduplication** - Stopped re-raising already-logged non-200 Umami responses so failed analytics calls emit one warning instead of noisy duplicate log lines
- **Next-race retry parity** - Moved `F1Service.get_next_race()` onto the shared Jolpica retry helper so the primary upcoming-race fetch now retries 429s the same way as the rest of the live API client
- **Local env example visibility** - Stopped ignoring `.env.local.example` so the checked-in local development template remains visible to contributors while `.env.local` stays untracked
- **Consistent dev extras** - Aligned `[project.optional-dependencies].dev` with `[dependency-groups].dev` so `pip install -e "[dev]"` and `uv sync --dev` install the same linting, typing, test, and data-science helper toolchain
- **Consistent byte formatting** - Matched the shared frontend `formatBytes()` helper to the backend decimal-unit formatter so cache/stat sizes render the same KB/MB/GB values across server and browser UI
- **Shared circuit metadata maps** - Moved the duplicated `CIRCUIT_ID_MAP` and `COUNTRY_MAP` constants into one shared module so renderer and service updates stay in sync across display modes and API helpers
- **Endpoint rate limiting** - Added lightweight per-IP in-memory throttling for BMP generation and `POST /api/perf-metrics` so expensive image renders and analytics ingestion have a basic abuse guard by default
- **Zero-valued stats aggregates** - Preserved legitimate `0.0` values in page, trend, aggregate, and 24-hour API stats payloads instead of collapsing them to `None`, so perfect vitals samples and zero-latency operational windows remain visible in dashboards
- **Safer BMP purge timing** - Deferred deletion of pregenerated BMP assets until after static next-race data is available, so a transiently missing race snapshot no longer wipes the last known-good BMP set before generation can begin
- **Persistent SQLite connections** - Reworked `Database` to reuse one loop-aware SQLite connection per instance instead of reopening and reconfiguring a fresh connection on every operation, and now close live database handles during app shutdown
- **Configurable GitHub API base URL** - Moved GitHub release/version requests onto `config.GITHUB_API_BASE_URL` so those external API calls follow the same environment-driven configuration path as the rest of the services
- **Renderer asset handle cleanup** - Wrapped disk-backed `Image.open()` calls for logos, tracks, flags, driver portraits, and team logos in context-managed loads so PIL image conversions keep returning detached images without leaking file handles during repeated preview and render jobs
- **Schedule event field naming** - Renamed the internal `ScheduleEvent` timestamp attribute to `event_datetime` while keeping `datetime` as a compatibility alias so model code no longer shadows the imported `datetime` type
- **Standings metadata extraction** - Moved season-facing driver-number and team-id lookup tables out of `api.py` into a shared utility module so route handlers no longer own that update-prone mapping data
- **BWR renderer helper deduplication** - Extracted shared track-image and results-header helpers for the BWR and BWRY renderers so multi-color calendar variants stop carrying near-identical fallback and flag-loading logic in parallel
- **Shared renderer text helpers** - Moved common team-layout and text-measurement helpers out of the 1-bit and Spectra 6 renderers so both primary renderer classes now reuse one source of truth for column splits, truncation, alignment, and team header formatting
- **Shared schedule label helpers** - Centralized localized session-name normalization and sprint-qualifying label formatting so the monochrome and Spectra 6 renderers no longer maintain duplicate schedule translation logic, and the Spectra 6 results footer now uses the canonical `session_qualifying` / `session_race` translation keys
- **Sprint-qualifying accent normalization** - Normalize `SprintQualifying`/`SprintShootout`/`Shootout` aliases before Spectra 6 accent-color lookup so sprint-qualifying schedule rows keep their dedicated highlight color instead of silently falling back to black
- **Shared logo/result text helpers** - Moved team-logo keying, logo crop utilities, and historical-result text fitting into shared renderer helpers so both primary renderers stop carrying duplicate asset-prep logic
- **Shared results header helper** - Centralized the year/flag footer header renderer so monochrome and Spectra 6 variants share one flag-loading path, including ISO fallback handling for countries like `UK`, `USA`, and `UAE`
- **Shared new-track message helper** - Moved the centered `NEW TRACK` footer message into a shared renderer helper so monochrome and Spectra 6 variants stop duplicating the same fallback layout math
- **Shared renderer font fallbacks** - Centralized Symbola, weather-icon, and Racing Sans One fallback loading so the primary renderers reuse one source of truth for icon and number font fallback behavior
- **Shared driver portrait helper** - Moved surname normalization, racing-number rendering, and portrait resize/paste logic into one shared helper so the monochrome and Spectra 6 team cards stop carrying duplicate driver-photo rendering code
- **Shared schedule-section helper** - Centralized schedule title rendering, row iteration, and countdown handoff so the monochrome and Spectra 6 calendar renderers reuse one schedule-section layout flow
- **Shared race-header helper** - Moved the logo/title split-header layout into one shared helper so monochrome and Spectra 6 race views now share the same title-block geometry and text positioning
- **Shared circuit-stats helper** - Centralized circuit fact composition and right-column stat-block layout so monochrome and Spectra 6 renderers stop duplicating the same lap/length/history rendering logic
- **Shared track-section helper** - Centralized circuit label layout and track-image placement so monochrome and Spectra 6 renderers now share the same left-column track-section geometry while keeping display-specific image preparation
- **Shared team-card layout helper** - Centralized the common team-row card geometry, meta-text placement, and logo container math so monochrome and Spectra 6 renderers now share the same card skeleton while keeping display-specific stats and driver-row rendering
- **Shared countdown-box helper** - Centralized race-status/countdown rendering and optional weather overlay layout so monochrome and Spectra 6 renderers now share one countdown/status box flow with only display colors passed in
- **Shared team stats/driver-row helpers** - Centralized the repeated team-card stats panel and driver-row rendering flow so monochrome and Spectra 6 renderers now only pass badge colors and text fills instead of maintaining duplicate row logic
- **Shared track asset loader** - Centralized source/fallback track asset resolution so monochrome and Spectra 6 renderers now share one loader path with only variant suffixes and fallback directories passed in
- **Shared results-section helper** - Centralized footer separator/new-track branching and qualifying/race column dispatch so monochrome and Spectra 6 renderers now share one historical-results section flow with only display colors and callbacks passed in
- **Shared multi-color results header helper** - Generalized the shared footer year/flag renderer to accept preferred flag-directory fallback chains and route footer rendering through overridable renderer hooks so B/W/R and B/W/R/Y variants reuse the shared header flow without bypassing their display-specific flag assets
- **Shared schedule-row helper** - Centralized schedule date/day/time parsing and row text layout so monochrome and Spectra 6 renderers now differ only in the session-name fill color passed into one shared row renderer
- **Shared track-stem helper** - Centralized circuit-id/location normalization for track asset candidate lookup so monochrome and Spectra 6 renderers now share the same race-data-to-track-stem resolution path before applying display-specific asset fallbacks
- **Removed team-logo wrapper methods** - Inlined the last display-specific team-logo callback wrappers into the shared team-card flow so monochrome and Spectra 6 renderers no longer carry separate `_draw_team_logo()` pass-through methods
- **Removed results-header wrapper methods** - Inlined the last monochrome and Spectra 6 results-header pass-through wrappers into the shared footer-section callback wiring so those renderers now call the shared year/flag helper directly
- **Removed layout utility wrapper methods** - Swapped remaining team-layout callsites and tests over to shared `split_teams_for_columns()`, `get_text_y()`, and `right_align_x()` helpers so the primary renderers no longer carry duplicate pass-through utilities for those calculations
- **Removed shared formatter wrapper methods** - Rewired remaining team-card/result callsites and tests to use shared `build_team_header_values()`, `format_team_driver_display_name()`, `format_points()`, `clamp_text()`, and `fit_result_text()` helpers directly so the primary renderers no longer keep utility pass-through methods for those common formatting paths
- **Removed shared logo helper wrapper methods** - Swapped remaining team-logo keying and crop callsites/tests over to shared `get_team_logo_key()`, `crop_to_content()`, and `crop_primary_horizontal_band()` helpers so the primary renderers no longer keep private pass-through utilities for logo preparation
- **Removed shared schedule i18n wrapper methods** - Rewired remaining schedule/session callsites and tests to use shared `format_schedule_session_name()`, `build_sprint_qualifying_label()`, and `translate_session_name()` helpers directly so the primary renderers no longer keep pass-through i18n wrappers for session labels
- **Shared F1 logo and track placeholder helpers** - Moved the last identical header-logo and missing-track placeholder drawing logic into `renderer_common` so monochrome and Spectra 6 renderers now share those visual fallbacks instead of keeping duplicate local helpers

### Security

#### Fixed

- **Optional operational API token hardening** - Followed up the original token-gated operational endpoints with release-ready validation, docs, and changelog coverage so deployments can lock down `/api/stats`, `/api/stats/history`, and `GET /api/perf-metrics` without breaking browser-side `POST /api/perf-metrics` web-vitals ingestion
- **Masked S3 credentials** - Stored S3 access keys as `SecretStr` values and resolved them only at S3 client creation time so accidental config logging and tracebacks no longer expose raw backup credentials
- **SITE_URL validation** - Added the shared URL validator to `SITE_URL` so malformed canonical host configuration falls back safely instead of leaking invalid redirect/canonical origins

### Development

#### Changed

- **Stable Python baseline** - Relaxed the project runtime/tooling baseline from pre-release Python `3.14.3` to stable Python `3.13` across packaging metadata, Docker images, CI workflows, and local setup docs
- **Compose quickstart refresh** - Switched the quickstart helper to the modern `docker compose` CLI and removed the obsolete top-level `version` field from `docker-compose.yml`
- **CI dependency caching and coverage** - Enabled pip dependency caching in the main CI workflow, installed the unified dev extras set directly, and turned on pytest coverage reporting so pull requests surface both test failures and app coverage regressions in one run
- **Example env sync** - Updated `.env.example` and `.env.local.example` to include the new GitHub API base URL, rate-limiting, operational API token, and statistics-retention settings so deployment templates match the current config surface
- **Native async pytest style** - Converted the async task and analytics test modules away from `asyncio.run()` wrappers to native `pytest.mark.asyncio` tests so they follow the configured strict asyncio test mode directly
- **Weather test async migration** - Converted the weather service test suite from nested `asyncio.run()` wrappers to native async pytest cases so async cache and forecast coverage now runs directly under the configured strict event-loop mode
- **Remaining async test migration** - Converted the shared HTTP client/retry, F1 season, standings, and database async test modules away from `asyncio.run()` wrappers so the remaining service-layer async coverage now runs directly under pytest-managed event loops
- **Stdlib timezone support** - Replaced `pytz` with a shared `zoneinfo` helper for config validation, request parameter checks, and race schedule conversion so timezone handling now uses the Python standard library across the app

## [1.2.19] - 2026-03-31

### Frontend

#### Changed

- **Calendar configure tooltip refresh** - Reworked the weather availability hint into a floating tooltip that follows the pointer, stays clear of the desktop sidebar, and remains accessible across mouse, keyboard, and touch interactions by using `aria-disabled` state instead of relying on native disabled-button hover behavior
- **Credits wording and layout sync** - Aligned homepage and desktop credits labels with the mobile drawer wording, expanded provider attribution rows, and standardized entries such as `Jolpica-F1 API`, `Weather-icons`, analytics, hosting, and device sources

### Backend

#### Changed

- **Public redirect hardening** - Switched the remaining public-page language redirects on home, configure, privacy, changelog, API docs, and stats routes to the validated `_redirect_path()` helper so canonical redirects keep only approved on-site targets and preserve the intended query handling

### Security

#### Fixed

- **Optional operational API token introduction** - Added the initial opt-in token checks for read-only operational metrics endpoints so deployments can lock down `/api/stats`, `/api/stats/history`, and `GET /api/perf-metrics` without breaking browser-side `POST /api/perf-metrics` web-vitals ingestion
- **Dependency vulnerability updates** - Bumped `pygments` in `uv.lock` and `picomatch` in `package-lock.json` to patched versions to clear the open GitHub security alerts on the release branch

### Development

#### Changed

- **Local artifact ignore rules** - Added `.playwright-cli/` to `.gitignore` so Playwright CLI snapshots and console captures do not get committed with future UI review iterations

## [1.2.18] - 2026-03-30

### Frontend

#### Added

- **Themed HTML 404 page** - Added a branded browser-facing 404 page with `noindex` metadata, localized navigation shortcuts, and direct rendering for unknown public HTML routes instead of JSON fallback responses

#### Changed

- **Canonical public-page normalization** - Public HTML routes now use explicit canonical redirects for language roots and trailing-slash variants so crawler-facing URLs stay stable across home, configure, stats, changelog, privacy, and API docs pages
- **Favicon asset serving** - Replaced the inline SVG emoji favicon with the packaged ICO asset so browser and audit tooling both see the same icon resource referenced by the site manifest and HTML head
- **404 and credits localization polish** - Localized the new 404 page copy across all supported languages, regrouped homepage credits into a stable responsive layout, and aligned site/README acknowledgements for weather data, weather icons, analytics, monitoring, flag assets, and inspiration sources
- **Homepage and mobile-nav polish** - Refined the homepage credits presentation, removed the desktop header credits dropdown, moved mobile credits into the drawer footer, and tightened mobile header spacing so the logo, menu button, and language switcher stay balanced across breakpoints
- **Language selector labels** - Shortened the visible `PT-BR` and `ZH-CN` language labels to compact `PT` and `ZH` display text while keeping the underlying locale routing unchanged
- **Header and drawer navigation cleanup** - Restored the smaller desktop logo/header balance, simplified the language switcher label treatment, and tuned the mobile drawer so navigation remains at the top while credits stay anchored in a compact footer block
- **Credits naming and layout sync** - Renamed credits entries to the current public wording (`Inspiration`, `Jolpica-F1 API`, `Weather-icons`, `Devices`) and aligned mobile drawer credits into consistent label/value rows with inline device and hosting pairs
- **README cleanup** - Removed decorative emoji section headings and refreshed credits/documentation copy so the public project page matches the current app behavior and deployment requirements

### Backend

#### Changed

- **Proxy-aware canonical redirects** - Disabled FastAPI's implicit slash redirects, added explicit 301 route normalizers, and taught the app/runtime to respect forwarded proxy headers so HTTPS canonical redirects no longer downgrade to `http` behind Coolify/Traefik
- **Canonical host enforcement** - Added `www` to apex redirect handling in middleware, hardened host parsing for non-default `SITE_URL` ports, and now emit HSTS on both normal HTTPS responses and the `www` redirect itself
- **Trusted proxy defaults** - Switched the container startup command to a configurable `FORWARDED_ALLOW_IPS` allowlist with private-network defaults, keeping proxy header trust adjustable per deployment instead of unconditional `*`
- **Startup config centralization** - Moved `FORWARDED_ALLOW_IPS` and `SKIP_PERSISTENCE_CHECK` into `app/config.py` so runtime defaults and environment loading stay in one configuration path

#### Fixed

- **Crawler and SEO endpoint coverage** - Added regression tests for `robots.txt`, `sitemap.xml`, canonical redirects, HTML 404 rendering, HSTS behavior, favicon responses, and HTTPS-only redirect assertions so the Search Console remediation work stays locked to the configured canonical host

### Documentation

#### Changed

- **Deployment env guidance** - Added missing required environment variables to the example env files and clarified that `SITE_URL` controls canonical URLs, Open Graph tags, `robots.txt`, `sitemap.xml`, and `www` canonicalization while `ANALYTICS_HOSTNAME` only affects analytics
- **Coolify apex redirect recipe** - Documented the production-ready Traefik and Caddy label setup for single-hop `http://www -> https://apex` canonical redirects on self-hosted Coolify deployments

## [1.2.17] - 2026-03-29

### Frontend

#### Added

- **Top-10 locale expansion** - Added localized site/UI coverage for `de`, `es`, `fr`, `it`, `nl`, `pl`, `pt-BR`, `tr`, `ja`, `zh-CN`, and `sk`, including locale metadata, alphabetized language selectors, and shared frontend locale handling

#### Changed

- **Localized configure/docs source-of-truth** - Moved configure-page UI copy into the shared translation files, injected canonical locale codes into shared frontend helpers, aligned language switching/redirect behavior with the current route and query string, and updated localized HTML API docs to expose the full supported language set
- **README and public docs refresh** - Updated README/examples/self-hosting guidance to match the current localized configure flow, teams endpoints, startup warmup behavior, generated localized preview assets, and API docs examples that now use the active request host instead of placeholder domains
- **Track artwork refresh** - Added new Miami, Albert Park, and Shanghai track source/variant assets for all supported display modes, including PSD source files for future edits

### Backend

#### Changed

- **Localized asset generation and startup warmup** - Expanded scheduled/startup generation to cover all configured locales for calendar and teams assets, kept timezone-specific calendar generation for popular non-default TZs, moved version refresh to hourly `:05`, and warmed teams render assets on startup to cut cold-start render latency
- **Pregenerated localized image serving** - `/calendar.bmp` and `/teams.bmp` now reuse pregenerated localized assets across all supported locales instead of special-casing only `en`/`cs`

#### Fixed

- **Localized page consistency** - Fixed localized configure/API docs routes, corrected `og:locale` metadata, aligned shared locale lists across backend/frontend, and localized configure race/session copy so non-`en`/`cs` pages no longer fall back to stale English strings
- **Renderer and preview reliability** - Added locale-aware CJK font coverage to image rendering, fit long translated schedule labels, shortened sprint qualifying schedule labels per locale, kept CJK teams driver names clear of the team-card header, restored teams previews/static loading paths, disabled invalid FastAPI preview response-model inference, and reused scheduler preview data across languages to avoid redundant fetches
- **Teams rendering polish** - Limited the CJK font override on teams screens to the translated header subtitle, kept the lower team cards on the brand font, and normalized Red Bull / Racing Bulls power-unit labels down to `RB ...` variants
- **Teams configure resiliency** - Fixed `Season Leaders`/teams configure loading regressions caused by page-specific JS assumptions and made teams previews prefer pregenerated localized PNGs with dynamic fallback
- **Security and release validation follow-ups** - Removed unsafe `innerHTML` usage from the ePaper setup flow, hardened client-side language redirects, fixed DeepSource validator findings, and kept changelog/release validation metadata in sync for release-readiness checks
- **Image key consistency** - Hardened pregenerated image-key helpers to reject unsupported display modes and aligned teams route cache keys with the shared scheduler/pregeneration key format

## [1.2.16] - 2026-03-25

### Backend

#### Fixed

- **Cancelled season refresh persistence** - Static season updates now preserve previously tracked cancelled races such as Bahrain and Jeddah when Jolpica omits them, and season JSON refreshes now end with a trailing newline for cleaner diffs and POSIX-friendly tooling

## [1.2.15] - 2026-03-24

### Frontend

#### Added

- **Teams display parity** - Added `1bit`, `B/W/R`, `B/W/R/Y`, and `Spectra 6` variants to the teams configure flow, previews, API docs, and a dedicated teams display breakdown in the stats dashboard
- **Display-aware session accents** - Multi-color calendar outputs now color `Race`, `Qualifying`, `Sprint`, and `Practice` labels according to the active display palette, with unsupported colors falling back to black
- **SVERIO hardware docs** - Added SVERIO `B/W/R/Y` PaperBoard hardware references to the site credits/README and documented a tested 7.5" `GDEM075F52` screenshot placeholder for the four-color panel

### Backend

#### Added

- **Teams display renderers** - Extended `/teams.bmp` to support the same display selection flow as `/calendar.bmp`, including color-aware teams rendering, per-display preview generation, analytics tagging, and scheduler/configure preview generation for all display modes

#### Fixed

- **Teams logo/render polish** - Refined logo sourcing and 1-bit/logo conversion behavior across teams renderers, added dedicated 1-bit overrides for problematic team marks, preserved half-points in standings output, and treated legacy teams analytics rows without `display_type` as `1bit`
- **Teams header layout** - Reworked the team-card header stats area into a shared aligned panel, improved vertical/horizontal centering, right-aligned points across teams, highlighted the leading constructor position in color-capable displays, and improved `B/W/R` and `B/W/R/Y` meta-text readability
- **Teams row alignment** - Fixed driver-name column alignment by anchoring names to a shared slot, centering driver numbers inside that slot, and keeping spacing consistent across mixed-width number/photo assets
- **Sauber logo fallback** - Non-`Spectra 6` teams renders now remap Sauber's green accent to a mono-friendly white-on-black mark so `1bit`, `B/W/R`, and `B/W/R/Y` variants stay legible in 2024/2025 layouts
- **Calendar session color mapping** - `Spectra 6` now uses red/yellow/green/blue accents for `Race`/`Qualifying`/`Sprint`/`Practice`, while `B/W/R` and `B/W/R/Y` gracefully fall back to supported palette colors
- **Benchmark and quality follow-ups** - Lazy-loaded teams assets for the 1-bit renderer to avoid unrelated benchmark regressions, restored CodSpeed to the GitHub-hosted runner, resolved DeepSource findings (including helper visibility/static-method cleanup), and split complex team-row renderer methods into smaller helpers
- **Documentation sync** - Updated README/examples/self-hosting/BMP pipeline docs so public documentation matches the current endpoints, display variants, generated preview naming, and teams rendering behavior

## [1.2.14] - 2026-03-19

### Frontend

#### Changed

- **Configure cleanup** - Split the shared configure screen into calendar/teams partials, removed dead teams timezone controls, renamed the teams side panel to `Season Leaders`, and tightened the mobile sidebar layout with compact display mode labels (`B/W`, `B/W/R`, `B/W/R/Y`, `6C`)
- **Stats dashboard polish** - Unified breakdown color ranking across cards, widened the display breakdown, clarified range/fallback copy, shortened mobile race labels to `GP`, and kept percentage bars aligned while preserving a visible white lowest-rank bar

### Backend

#### Changed

- **Stats shaping cleanup** - Removed unused display color metadata and dropped unused performance dashboard queries from the stats page route while keeping localized range labels in the rendered context

#### Fixed

- **Race stats aggregation** - Combined auto-selected and manually selected requests for the same race into a single breakdown row while retaining an `auto_selected_count` signal for display hints

## [1.2.13] - 2026-03-17

### Frontend

#### Added

- **B/W/R/Y display mode** - Added `display=bwry` to the calendar configure UI, preview routing, API docs, and stats labeling for the four-color calendar output

### Backend

#### Added

- **B/W/R/Y renderer pipeline** - Added dedicated `bwry` calendar rendering, scheduler generation, track/flag preprocessing scripts, and calendar API support alongside existing `1bit`, `bwr`, and `spectra6` modes

#### Fixed

- **SEO: lightweight HTML HEAD responses** - Public HTML page routes now short-circuit `HEAD` requests before analytics or stats queries, preventing 405 responses in Search Console without adding crawler-only pageview noise or extra DB load

## [1.2.12] - 2026-03-16

### Frontend

#### Changed

- **Track asset variants** - Track rendering now prefers display-specific source assets like `_bw`, `_bwr`, and `_spectra6`, with the plain circuit PNG kept as the generic fallback and `_bwry` naming prepared for future display support
- **Track render quality** - `1bit`, `B/W/R`, and `Spectra 6` track maps now prefer source artwork over preprocessed BMP fallbacks so resized text and thin lines stay sharper in the final composed image
- **Display palettes** - `B/W/R` now uses a vivid pure red palette and `Spectra 6` now uses more vivid red/yellow/green/blue output colors to better match the prepared source artwork
- **BMP pipeline docs** - Added a dedicated `BMP_PROCESSING.md` guide that documents the current source naming, preprocessing steps, fallback order, and final BMP encoding flow for all active and prepared display variants

### Backend

#### Removed

- **`main` -> `dev` sync automation** - Removed the post-release sync workflow and retired the dedicated `dev` branch maintenance flow

#### Changed

- **Track preprocessing scripts** - Added explicit preprocessing flows for `spectra6` and prepared `bwry` track assets, and aligned runtime/source resolution around per-display source variants with generic fallback assets

#### Fixed

- **Post-merge quality issues** - Added Open-Meteo config validation at startup, cleaned up DeepSource-reported service/test patterns, and documented the safe `0.0.0.0` bind used for container deployments
- **Cancelled race persistence** - Season API responses now merge cancelled races back in from static season JSON when Jolpica omits them entirely, so removed Bahrain/Jeddah weekends still appear at the end of the calendar as cancelled entries

## [1.2.11] - 2026-03-16

### Frontend

#### Changed

- **Cancelled race selection** - Configure UI now keeps cancelled weekends at the end of the season list, highlights them in red, and lets previews request them via stable `race_key` identifiers

### Backend

#### Changed

- **F1 data automation** - Scheduled `Update F1 Data` runs now refresh both historical circuit results and static season calendar JSON files under `app/assets/seasons/`

#### Fixed

- **Race status rendering** - `/calendar.bmp` keeps cancelled races addressable without breaking existing `year` + `round` requests, skips weather fetches for cancelled weekends, renders a centered `CANCELLED`/`ZRUŠENO` label in the countdown slot, and now shows `IN PROGRESS`/`PROBÍHÁ` for the first three hours after lights out before switching to `COMPLETED`/`DOKONČEN`

## [1.2.10] - 2026-03-13

### Frontend

#### Added

- **Stats display breakdown** - Added a new statistics card that shows calendar request share by `1bit`, `B/W/R`, and `Spectra 6` display type

### Backend

- **Display type analytics** - Persisted calendar display mode in API call statistics so the stats dashboard can aggregate usage by display type

## [1.2.9] - 2026-03-13

### Frontend

#### Added

- **B/W/R display mode** - Added `display=bwr` to the configure UI and preview routing for black/white/red calendar output

### Backend

- **B/W/R renderer pipeline** - Added dedicated B/W/R rendering, scheduler generation, asset preprocessing scripts, and calendar API support alongside existing `1bit` and `spectra6` modes

## [1.2.8] - 2026-03-11

### Frontend

#### Fixed

- **Track map assets** - Refreshed `albert_park` and `shanghai` source track maps to improve circuit clarity in calendar previews

### Backend

#### Changed

- **Pre-processed track bitmaps** - Regenerated `albert_park.bmp` and `shanghai.bmp` in `tracks_processed` from the updated source maps to keep 1-bit rendering output aligned
- **Spectra 6 track bitmap** - Updated `tracks_spectra6/albert_park.bmp` to match the refreshed Albert Park circuit source
- **Release sync automation** - Updated release and sync workflows to publish releases with `SYNC_PAT`, trigger main-to-dev sync after published releases, and record the synced `main` SHA in the automation PR

## [1.2.7] - 2026-03-09

### Frontend

#### Fixed

- **Teams & Drivers preview** - Corrected 2026 team ordering, standings placement, right-aligned logos, and season selector behavior so the configure preview matches the live championship layout
- **Driver number rendering** - Switched `teams.bmp` to render cleaner font-based driver numbers instead of relying on stale number image assets

### Backend

#### Fixed

- **2026 teams bitmap data** - Added curated 2026 team data, corrected driver numbers, and normalized override handling so live and static team renders stay in sync
- **Teams bitmap rendering** - Team logos now support Audi and Cadillac, constructor standings render in the correct order for 11-team 2026 grids, and team header layout avoids collisions with standings and driver names
- **Teams bitmap freshness** - `/teams.bmp` now disables long-lived HTTP caching so lineup and standings changes appear immediately after deploys
- **Teams data matching** - Driver standings merging now tolerates ASCII/diacritic name differences and sponsor-prefixed constructor names such as `Perez`/`Pérez`, `Hulkenberg`/`Hülkenberg`, and `Atlassian Williams`/`Williams`

#### Added

- **2026 regression coverage** - Added renderer and teams-service tests for 2026 team data, manual driver-number overrides, and full BMP rendering in English and Czech

## [1.2.6] - 2026-03-01

### Backend

#### Changed

- **Python runtime baseline** - Raised minimum supported version to `3.14.3` and aligned Ruff target to `py314`
- **Docker runtime parity** - Updated builder and runtime images to `python:3.14.3-slim`
- **CI runtime parity** - Updated workflows to run on Python `3.14.3` and moved CI dependency install to `pip install -e ".[dev]"`
- **Dependency lock refresh** - Regenerated `uv.lock` for `requires-python = ">=3.14.3"` with compatible resolved versions
- **Docs/runtime alignment** - Updated README and deployment docs to reflect the new Python baseline and current API surface

---

## [1.2.5] - 2026-03-01

### Backend

#### Added

- **CodSpeed benchmark workflow** - Added `.github/workflows/codspeed.yml` to run renderer benchmarks on pull requests and pushes to `main`
- **Renderer performance benchmarks** - Added benchmark coverage for 1-bit and Spectra 6 renderers (`calendar`, `calendar + historical`, `teams/drivers`, `standings`, `error`) in `tests/test_benchmarks.py`

#### Changed

- **Benchmarking dependencies** - Added `pytest-codspeed` to dev dependencies and updated the lockfile for reproducible benchmark runs

---

## [1.2.4] - 2026-02-25

### Frontend

#### Added

- **Open-Meteo attribution** - Added Open-Meteo to the Credits list in both desktop header dropdown and mobile configure sidebar

### Backend

#### Fixed

- **Forecast range boundary** - Race-day weather now requests a forecast window that always includes the race date (fixes missing data near the 14-day boundary)
- **Nearest-hour fallback** - When exact race start hour is unavailable, fallback now picks the closest hour on race day instead of the first hour of that day

#### Added

- **Weather regression tests** - Added coverage for forecast-days window calculation and nearest-hour fallback selection
- **Release automation** - Added CI workflow that creates GitHub releases from the latest `CHANGELOG.md` entry after pushes to `main`
- **Main→dev sync automation** - Added CI workflow that opens/updates an auto-merge PR to sync `main` back into `dev` after each push to `main`

---

## [1.2.3] - 2026-02-25

### Frontend

#### Fixed

- **Race day weather controls** - Configure page now enables/disables race-day weather based on the selected race date (14-day forecast window)
- **Desktop/mobile sync** - Race selection now re-checks race-day weather availability on both desktop and mobile controls
- **Disabled button states** - Race-day buttons now use consistent disabled styling with `aria-disabled` for better UX and accessibility

### Backend

#### Changed

- **Weather context helper** - Added `get_weather_context()` to centralize current and race-day forecast loading
- **Race-day forecast rendering** - `/calendar.bmp` and preview generation now use dedicated race-day forecast data instead of reusing current weather
- **`weather_type` alias support** - Added handling for `race` alias in weather rendering logic for both 1-bit and Spectra 6 countdown labels
- **Version metadata** - `last_updated` now reflects the latest main-branch commit timestamp

#### Fixed

- **Privacy i18n test stability** - Updated cookie handling in test client to make language preference checks deterministic

---

## [1.2.2] - 2026-01-12

### Frontend

#### Changed

- **Language URLs** - Switched from query parameters (`?lang=cs`) to subdirectory URLs (`/cs/`) for better SEO
- **Automatic redirects** - Old `?lang=` URLs now redirect (301) to new subdirectory format
- **Language switcher** - Updated to navigate between subdirectory URLs

### Backend

#### Added

- **Subdirectory language routing** - All HTML pages now support `/{lang}/` prefix (e.g., `/cs/privacy`, `/cs/configure/calendar`)
- **Language URL helpers** - New `lang_url()` function for generating language-aware URLs in templates

#### Changed

- **Sitemap** - Now includes all language variants with subdirectory URLs
- **Hreflang tags** - Updated to reference subdirectory URLs instead of query parameters
- **BMP endpoints unchanged** - API endpoints (`/calendar.bmp`, `/teams.bmp`) still use `?lang=` parameter

#### Fixed

- **Router ordering** - Reordered routers so `/preview/*` routes are matched before `/{lang}/*` patterns
- **Service Worker** - Removed non-existent `favicon.svg` from cache list (was causing SW install failure)
- **Configure page preload** - Removed unused image preload that didn't match dynamically loaded variants

---

## [1.2.1] - 2026-01-12

### Backend

#### Fixed

- **Spectra 6 PNG previews** - Configure page previews now display colors instead of grayscale
- **SEO: Sitemap HEAD support** - `/sitemap.xml` and `/robots.txt` now respond to HEAD requests (fixes Google Search Console)
- **SEO: Canonical URLs** - Sitemap now uses clean URLs without `?lang=` parameter

---

## [1.2.0] - 2026-01-11

### API

#### Added

- `display` parameter for `/calendar.bmp` - set to `spectra6` for 6-color E-Ink displays (default: `1bit`)

### Frontend

#### Added

- **Display type selector** - Choose between 1-BIT (monochrome) and 6-COLOR (Spectra 6) display modes
- **Mobile display controls** - Full display type settings available on mobile sidebar
- **Pre-rendered preview variants** - Configure page now uses pre-rendered PNG for all weather/display combinations

#### Fixed

- **SEO** - Invalid or empty `?lang=` parameter now redirects to canonical URL (301)
- **Language preference** - Stored language preference now applied correctly on page load
- **Weather buttons** - Fixed visibility in desktop mode
- **Preview weather sync** - Weather OFF/current/race buttons now show correct preview image

### Backend

#### Added

- **Spectra 6 Renderer** - New 840-line renderer for 7.3" 6-color E-Ink displays (800×480)
- **6-color palette** - BLACK, WHITE, RED (#A02020), YELLOW (#F0E050), GREEN (#608050), BLUE (#5080B8)
- **Pre-processed track images** - 25 circuit maps optimized for Spectra 6 (494×271, no dithering)
- **Pre-processed flag images** - 26 country flags for Spectra 6 (87×58)
- **Dedicated F1 logo** - Color-optimized logo for Spectra 6 displays
- **Weather pre-fetch** - Scheduler fetches weather at :55 before hourly image generation
- **DeepSource integration** - Automated code quality analysis
- **PNG preview variants** - Scheduler generates preview PNG for all weather/display combinations

#### Changed

- **Scheduler** - Pre-generates all variants: 2 displays × 3 weather modes × 2 languages
- **Session colors** - Simplified for Spectra 6: only "Race" is RED, all others BLACK
- **Red accent lines** - F1 logo underline and results separator now use RED in Spectra 6
- **Code quality** - Added @staticmethod decorators to pure utility methods
- **Short countdown labels** - Uses "d"/"h" instead of "days"/"hours" for current/race_day weather types
- **Unified weather cache** - Image generation now uses same in-memory cache as dynamic rendering

#### Fixed

- **CIRCUIT_ID_MAP** - Added Vegas → Las Vegas mapping for track images
- **Country code logic** - Removed redundant fallback (already in COUNTRY_MAP)
- **Weather config** - Added missing weather configuration attributes
- **Track centering** - Spectra 6 renderer now centers track images horizontally and vertically
- **Pre-generated image selection** - Correctly selects BMP file based on weather_type parameter

---

## [1.1.4] - 2026-01-04

### API

#### Added

- `weather` parameter for `/calendar.bmp` - set to `false` to disable weather display
- `weather_type` parameter - `current` for current weather, `race_day` for race day forecast (default)

### Frontend

#### Added

- **Weather display in countdown box** - Shows weather icon, temperature, and precipitation chance
- **Weather Icons font** - Professional weather icons from erikflowers/weather-icons (SIL OFL 1.1)
- **Weather type selector** - Choose between current weather or race day forecast
- **Mobile weather controls** - Full weather settings available on mobile sidebar

#### Changed

- **Countdown box redesign** - Now shows `🏁 5D 3H` format with weather on the right
- **Circuit stats simplified** - Only shows track length, fastest lap, and First GP (weather moved to countdown)
- **Configure page** - Weather section with neobrutalism-styled toggle buttons (OFF/CURRENT/RACE DAY)

### Backend

#### Added

- `WeatherService` class with Open-Meteo API integration
- `WeatherData` dataclass with icon, temperature, and precipitation properties
- Weather icon font loader (`_load_weather_icon_font`)
- Race day weather forecast (up to 14 days ahead)
- Current weather fallback when race day forecast unavailable
- Weather caching (60 minutes default)

#### Changed

- `render_calendar()` now accepts `weather_data` parameter
- `_draw_countdown_box()` renders weather alongside countdown
- `_draw_circuit_stats()` simplified - removed weather row
- Weather params included in BMP cache key

---

## [1.1.3] - 2026-01-02

### Backend

#### Fixed

- **Font MIME Type** - TTF fonts served as `text/plain` instead of `font/ttf` - registered correct MIME types

---

## [1.1.2] - 2026-01-02

### Backend

#### Fixed

- **Font Loading** - SpaceMono TTF files were corrupted (HTML instead of binary) - re-downloaded from Google Fonts
- **SEO** - Fixed hreflang x-default to include `?lang=en` for consistency with canonical URLs
- **Performance** - Removed oversized favicon.svg (240KB embedded PNG) - using PNG favicon instead

---

## [1.1.1] - 2026-01-02

### Frontend

#### Fixed

- **LCP Optimization** - First preview image now has `fetchpriority="high"` and no lazy loading
- **CLS Prevention** - Added explicit `width="800" height="480"` to preview images
- **Logo Optimization** - Removed oversized 2x srcset (19KB → 10KB saved), added `fetchpriority="high"`
- **Render-blocking** - Deferred `common.js` script loading

#### Accessibility

- **WCAG AA Contrast** - Fixed low contrast text in credits section (`text-gray-400` → `text-gray-600`)
- **WCAG AA Contrast** - FoxeeLab link now uses `font-bold` with hover state instead of `text-racing-red`
- **Screen Readers** - Added `aria-label` to mobile menu buttons with fallback (`nav.get('nav_menu', 'Menu')`)
- **Screen Readers** - Added `aria-hidden="true"` to decorative SVG icons
- **Screen Readers** - Added `sr-only` label to language switcher select

#### SEO

- **Canonical URLs** - Now correctly use `request.url.path` with language parameter
- **Hreflang Tags** - Fixed to match canonical URL structure per page

---

## [1.1.0] - 2026-01-01

### API

#### Added

- `/teams.bmp` endpoint for teams & drivers grid
- `/api/teams/{year}` endpoint for team data (JSON)
- `/api/standings/leader` endpoint for championship leaders (JSON)
- `/api/perf` endpoint collects Web Vitals (LCP, FCP, CLS, TTFB, INP) from browsers

### Frontend

#### Added

- **New landing page** with screen type selection (Calendar/Teams)
- **Teams & Drivers screen** - Full team grid with driver photos and points
- **Teams panel sidebar** on homepage showing championship leaders
- **Configure page** (`/configure/{screen}`) for interactive preview of each screen type
- **Percentile Stats** - p50/p75/p95 percentiles for Core Web Vitals with color-coded thresholds
- **Service Worker** - Offline caching for static assets (`/static/*`) with cache-first strategy
- **Critical CSS Inlining** - Above-the-fold CSS inlined in `<head>` for faster first paint

#### Changed

- Redesigned homepage with screen type selector
- Improved teams screen layout with full driver names and enlarged photos
- Driver numbers now display in Racing Sans font when photo is missing
- Team logos centered with fixed container sizing
- Drivers sorted by points within each team
- Stats page Web Vitals section now displays percentile table instead of simple averages
- Stylesheet loading now uses preload/onload pattern with noscript fallback

#### Fixed

- **Mobile navigation** - Added missing nav links (Stats, API, Privacy, Changelog) to configure page sidebar
- Driver number font size increased to 22px for better proportion
- Left-aligned driver positions with enlarged P1-P3 badges
- Preview position now maintains consistent spacing
- Removed duplicate logo from homepage
- Fixed Zivyobraz.eu capitalization

### Backend

#### Added

- Team logos integration in teams service
- **GZip Compression** - Response compression middleware for responses >=500 bytes
- `get_perf_trends()` method in database service for hourly metric aggregation
- Percentile calculation (`_calculate_percentile()`) for performance statistics

#### Changed

- `get_perf_stats()` now returns p50/p75/p95 percentiles for each metric

#### Fixed

- **Teams "Current" season logic** - Now correctly returns 2025 data until March 8, 2026 (season start) instead of using calendar year

---

## [1.0.2] - 2025-12-30

### Backend

#### Security

- **Fix 7 CodeQL path injection vulnerabilities** (high severity) in `i18n.py`, `f1_service.py`, `main.py`

---

## [1.0.1] - 2025-12-28

### Backend

#### Security

- Add timezone, language, and year validation to prevent path injection attacks
- Add `permissions: contents: read` to CI workflow

#### Added

- **Startup check for persistent storage** - Creates `.persistence_marker` file, warns if storage is non-persistent

---

## [1.0.0] - 2025-12-26

Initial public release.

### API

#### Added

- `/calendar.bmp` endpoint for E-Ink calendar image
- `lang` parameter for language selection (cs/en)
- `tz` parameter for timezone conversion
- `year` and `round` parameters for specific race selection

### Frontend

#### Added

- **Neobrutalist UI redesign** with Space Mono font, neo-brutalist shadows, and black borders
- **Persistent header** with navigation links (GitHub, API, Privacy, Credits dropdown)
- **Mobile-responsive layout** with hamburger menu for small screens
- **Timezone selector** with continent filters and search functionality
- **Auto-detection** of user's timezone in browser
- **Credits dropdown** in header with links to all third-party services and inspiration
- **Privacy Policy page** (`/privacy`) with multi-language support (EN/CS)
- **Interactive API documentation** (`/api/docs/html`) with styled UI and code examples
- 800x480 1-bit BMP calendar image for E-Ink displays
- Multi-language support (Czech and English)
- Historical race results display (previous year's podium)
- Circuit statistics display (length, laps, first GP)
- Track map rendering

#### Changed

- Footer removed - all links moved to persistent header navigation
- UI language detection from `Accept-Language` header with `?lang=` override
- Improved mobile layout with collapsible sidebar instead of inline controls

#### Fixed

- Header title now clickable link to homepage on all pages
- Hamburger menu now properly closes with overlay click and close button
- Missing timezone label element added for timezone display

### Backend

#### Added

- FastAPI backend with async support
- F1 race data fetching from Jolpica API
- Timezone conversion (UTC to configurable timezone)
- Umami analytics integration
- Sentry/GlitchTip error monitoring
- Docker and Docker Compose support
- Coolify deployment support
- **SQLite-based API call logging** with data transfer statistics
- **Complete favicon set**: SVG, ICO (48x48), Apple Touch Icon (180x180), PWA manifest icons
- New translations: `laps`, `first_gp`, `circuit_not_available`, `contact_github`, privacy policy texts

#### Changed

- F1Service now accepts timezone parameter
- Database paths now use absolute paths (`/app/data/`) for Docker container compatibility

#### Fixed

- Database persistence with absolute paths for Docker deployments
- HTML structure with proper `<main>` element nesting
- `toggleSidebar()` function added to main page JavaScript
- Umami analytics task reference retention for proper async tracking
- Circuit ID mapping: `vegas` now correctly maps to `las_vegas` for circuit stats
- Moved debug scripts to `scripts/` directory
- Cleaned up root directory (removed test files, old Dockerfile)
