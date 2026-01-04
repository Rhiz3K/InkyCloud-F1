# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
