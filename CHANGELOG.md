# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Frontend

#### Added

- **Teams display parity** - Added `1bit`, `B/W/R`, `B/W/R/Y`, and `Spectra 6` variants to the teams configure flow, previews, API docs, and a dedicated teams display breakdown in the stats dashboard

### Backend

#### Added

- **Teams display renderers** - Extended `/teams.bmp` to support the same display selection flow as `/calendar.bmp`, including color-aware teams rendering, per-display preview generation, and analytics tagging

#### Fixed

- **Teams logo/render polish** - Refined logo sourcing and 1-bit/logo conversion behavior across teams renderers, preserved half-points in standings output, and treated legacy teams analytics rows without `display_type` as `1bit`

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
