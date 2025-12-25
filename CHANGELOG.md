# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-12-26

Initial public release.

### Added
- **Neobrutalist UI redesign** with Space Mono font, neo-brutalist shadows (`shadow-neo`), and black borders
- **Persistent header** with navigation links (GitHub, API, Privacy, Credits dropdown)
- **Mobile-responsive settings sidebar** with hamburger menu for small screens
- **Timezone continent filters** for easier timezone selection by region
- **Credits dropdown** in header with links to all third-party services and inspiration
- **Complete favicon set**: SVG, ICO (48x48), Apple Touch Icon (180x180), PWA manifest with 192x192 and 512x512 icons
- **Privacy Policy page** (`/privacy`) with multi-language support (EN/CS)
- **Interactive HTML API documentation** (`/api/docs/html`) with styled UI
- **SQLite-based API call logging** with data transfer statistics
- **Umami analytics tracking** for direct `/calendar.bmp` access with query parameter support
- Timezone support: `tz` parameter for `/calendar.bmp` endpoint
- Timezone selector with search on preview page
- Auto-detection of user's timezone in browser
- New translations: `laps`, `first_gp`, `circuit_not_available`, `contact_github`, privacy policy texts

### Changed
- **Footer removed** - all links moved to persistent header navigation
- UI language detection from `Accept-Language` header with `?lang=` override
- Improved mobile layout with collapsible sidebar instead of inline controls
- F1Service now accepts timezone parameter
- Database paths now use absolute paths (`/app/data/`) for Docker container compatibility
- Updated API documentation in README

### Fixed
- Database persistence with absolute paths for Docker deployments
- Header title now clickable link to homepage on all pages
- HTML structure with proper `<main>` element nesting in Content Wrapper
- `toggleSidebar()` function added to main page JavaScript
- Missing `id="tzLabel"` element added for timezone display
- Hamburger menu now properly closes with overlay click and close button
- Umami analytics task reference retention for proper async tracking
- Circuit ID mapping: `vegas` now correctly maps to `las_vegas` for circuit stats
- Moved debug scripts to `scripts/` directory
- Cleaned up root directory (removed test files, old Dockerfile)

## [0.1.0] - 2025-12-20

### Added
- Initial release
- 800x480 1-bit BMP generation for E-Ink displays
- FastAPI backend with async support
- F1 race data fetching from Jolpica API
- Timezone conversion (UTC to configurable timezone)
- i18n support (Czech and English)
- Historical race results display
- Circuit statistics display
- Track map rendering
- Umami analytics integration
- Sentry/GlitchTip error monitoring
- Docker and Docker Compose support
- Coolify deployment support
