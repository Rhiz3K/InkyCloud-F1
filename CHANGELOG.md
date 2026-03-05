# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.7] - 2026-03-05

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Changed

- **Albert Park track assets** - Replaced `app/assets/tracks/albert_park.png` with the latest map version and regenerated `app/assets/tracks_processed/albert_park.bmp`

</details>

---

## [1.2.6] - 2026-03-01

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Changed

- **Python runtime baseline** - Raised minimum supported version to `3.14.3` and aligned Ruff target to `py314`
- **Docker runtime parity** - Updated builder and runtime images to `python:3.14.3-slim`
- **CI runtime parity** - Updated workflows to run on Python `3.14.3` and moved CI dependency install to `pip install -e ".[dev]"`
- **Dependency lock refresh** - Regenerated `uv.lock` for `requires-python = ">=3.14.3"` with compatible resolved versions
- **Docs/runtime alignment** - Updated README and deployment docs to reflect the new Python baseline and current API surface

</details>

---

## [1.2.5] - 2026-03-01

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Added

- **CodSpeed benchmark workflow** - Added `.github/workflows/codspeed.yml` to run renderer benchmarks on pull requests and pushes to `main`
- **Renderer performance benchmarks** - Added benchmark coverage for 1-bit and Spectra 6 renderers (`calendar`, `calendar + historical`, `teams/drivers`, `standings`, `error`) in `tests/test_benchmarks.py`

#### Changed

- **Benchmarking dependencies** - Added `pytest-codspeed` to dev dependencies and updated the lockfile for reproducible benchmark runs

</details>

---

## [1.2.4] - 2026-02-25

### Frontend

#### Added

- **Open-Meteo attribution** - Added Open-Meteo to the Credits list in both desktop header dropdown and mobile configure sidebar

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Fixed

- **Forecast range boundary** - Race-day weather now requests a forecast window that always includes the race date (fixes missing data near the 14-day boundary)
- **Nearest-hour fallback** - When exact race start hour is unavailable, fallback now picks the closest hour on race day instead of the first hour of that day

#### Added

- **Weather regression tests** - Added coverage for forecast-days window calculation and nearest-hour fallback selection
- **Release automation** - Added CI workflow that creates GitHub releases from the latest `CHANGELOG.md` entry after pushes to `main`
- **Main→dev sync automation** - Added CI workflow that opens/updates an auto-merge PR to sync `main` back into `dev` after each push to `main`

</details>

---

## [1.2.3] - 2026-02-25

### Frontend

#### Fixed

- **Race day weather controls** - Configure page now enables/disables race-day weather based on the selected race date (14-day forecast window)
- **Desktop/mobile sync** - Race selection now re-checks race-day weather availability on both desktop and mobile controls
- **Disabled button states** - Race-day buttons now use consistent disabled styling with `aria-disabled` for better UX and accessibility

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Changed

- **Weather context helper** - Added `get_weather_context()` to centralize current and race-day forecast loading
- **Race-day forecast rendering** - `/calendar.bmp` and preview generation now use dedicated race-day forecast data instead of reusing current weather
- **`weather_type` alias support** - Added handling for `race` alias in weather rendering logic for both 1-bit and Spectra 6 countdown labels
- **Version metadata** - `last_updated` now reflects the latest main-branch commit timestamp

#### Fixed

- **Privacy i18n test stability** - Updated cookie handling in test client to make language preference checks deterministic

</details>

---

## [1.2.2] - 2026-01-12

### Frontend

#### Changed

- **Language URLs** - Switched from query parameters (`?lang=cs`) to subdirectory URLs (`/cs/`) for better SEO
- **Automatic redirects** - Old `?lang=` URLs now redirect (301) to new subdirectory format
- **Language switcher** - Updated to navigate between subdirectory URLs

### Backend

<details markdown="1">
<summary>Backend</summary>

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

</details>

---

## [1.2.1] - 2026-01-12

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Fixed

- **Spectra 6 PNG previews** - Configure page previews now display colors instead of grayscale
- **SEO: Sitemap HEAD support** - `/sitemap.xml` and `/robots.txt` now respond to HEAD requests (fixes Google Search Console)
- **SEO: Canonical URLs** - Sitemap now uses clean URLs without `?lang=` parameter

</details>

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

<details markdown="1">
<summary>Backend</summary>

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

</details>

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

<details markdown="1">
<summary>Backend</summary>

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

</details>

---

## [1.1.3] - 2026-01-02

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Fixed

- **Font MIME Type** - TTF fonts served as `text/plain` instead of `font/ttf` - registered correct MIME types

</details>

---

## [1.1.2] - 2026-01-02

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Fixed

- **Font Loading** - SpaceMono TTF files were corrupted (HTML instead of binary) - re-downloaded from Google Fonts
- **SEO** - Fixed hreflang x-default to include `?lang=en` for consistency with canonical URLs
- **Performance** - Removed oversized favicon.svg (240KB embedded PNG) - using PNG favicon instead

</details>

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

<details markdown="1">
<summary>Backend</summary>

#### Added

- Team logos integration in teams service
- **GZip Compression** - Response compression middleware for responses >=500 bytes
- `get_perf_trends()` method in database service for hourly metric aggregation
- Percentile calculation (`_calculate_percentile()`) for performance statistics

#### Changed

- `get_perf_stats()` now returns p50/p75/p95 percentiles for each metric

#### Fixed

- **Teams "Current" season logic** - Now correctly returns 2025 data until March 8, 2026 (season start) instead of using calendar year

</details>

---

## [1.0.2] - 2025-12-30

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Security

- **Fix 7 CodeQL path injection vulnerabilities** (high severity) in `i18n.py`, `f1_service.py`, `main.py`

</details>

---

## [1.0.1] - 2025-12-28

### Backend

<details markdown="1">
<summary>Backend</summary>

#### Security

- Add timezone, language, and year validation to prevent path injection attacks
- Add `permissions: contents: read` to CI workflow

#### Added

- **Startup check for persistent storage** - Creates `.persistence_marker` file, warns if storage is non-persistent

</details>

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

<details markdown="1">
<summary>Backend</summary>

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

</details>
