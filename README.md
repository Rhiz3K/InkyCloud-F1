# F1 E-Ink Calendar

**Free F1 race calendar for your E-Ink display!** Use the public instance at **[f1.inkycloud.click](https://f1.inkycloud.click)** — no setup required.

[![Public Demo](https://img.shields.io/badge/Public_Demo-f1.inkycloud.click-E10600?style=for-the-badge)](https://f1.inkycloud.click)
[![Self-Host](https://img.shields.io/badge/Self--Host-Guide-6C47FF?style=for-the-badge&logo=docker&logoColor=white)](./SELF-HOSTING.md)
[![CodSpeed](https://img.shields.io/badge/CodSpeed-Performance-0A7BFF?style=for-the-badge)](https://codspeed.io/Rhiz3K/InkyCloud-F1?utm_source=badge)

---

## Quick Start — Use It Now!

The easiest way to display the F1 calendar on your E-Ink device is to use our **free public instance**:

### For [zivyobraz.eu](https://zivyobraz.eu) Users

1. Register at [zivyobraz.eu](https://zivyobraz.eu) and add your ePaper device
2. In device settings, select **"URL"** as content source
3. Enter the calendar URL:
   ```
   https://f1.inkycloud.click/calendar.bmp?lang=cs
   ```
4. Done! Your E-Ink display will show the next F1 race 🏁

### URL Parameters

| Parameter      | Options                          | Example                              |
| -------------- | -------------------------------- | ------------------------------------ |
| `lang`         | `cs`, `de`, `en`, `es`, `fr`, `it`, `ja`, `nl`, `pl`, `pt-BR`, `sk`, `tr`, `zh-CN` | `?lang=sk` |
| `tz`           | Any IANA timezone                | `?tz=America/New_York`               |
| `year`         | Season year                      | `?year=2026`                         |
| `round`        | Race round number                | `?year=2026&round=5`                 |
| `race_key`     | Specific race key from `/api/races/{year}` (requires `year`) | `?year=2026&race_key=2026-round-5-monaco-2026-05-24` |
| `display`      | `1bit`, `bwr`, `bwry`, `spectra6` | `?display=bwry`                      |
| `weather`      | `true`, `false`                  | `?weather=false`                     |
| `weather_type` | `race_day`, `race`, `current`, `off` | `?weather=true&weather_type=current` |

**Examples:**

```text
https://f1.inkycloud.click/calendar.bmp?lang=cs
https://f1.inkycloud.click/calendar.bmp?lang=en&tz=America/New_York
https://f1.inkycloud.click/calendar.bmp?lang=en&year=2026&round=5
https://f1.inkycloud.click/calendar.bmp?lang=en&year=2026&race_key=2026-round-5-monaco-2026-05-24
https://f1.inkycloud.click/calendar.bmp?lang=en&display=bwr
https://f1.inkycloud.click/calendar.bmp?lang=en&display=bwry
https://f1.inkycloud.click/calendar.bmp?lang=en&display=spectra6
https://f1.inkycloud.click/calendar.bmp?lang=en&weather=true&weather_type=current
https://f1.inkycloud.click/teams.bmp?lang=ja&display=spectra6
https://f1.inkycloud.click/sk/configure/calendar
```

---

## Preview

![F1 E-Ink Calendar Preview](./assets/device.jpg)

_LaskaKit 7.5" E-Ink display showing F1 race calendar in Czech_

![SVERIO B/W/R/Y](./assets/device_sverio_bwry.png)

_SVERIO PaperBoard 7.5" GDEM075F52 four-color 800×480 ePaper (black/white/yellow/red)_

---

## Features

- **800x480 BMP output** — `1bit` monochrome, `bwr` B/W/R, `bwry` B/W/R/Y, and `spectra6` 6-color mode for both calendar and teams screens
- **Teams & Drivers screen** — Dedicated `teams.bmp` render for the default or selected season with constructor lineup, driver photos, and championship points
- **Localized UI and assets** — Routing, configure pages, previews, docs, and pregenerated BMPs support `cs`, `de`, `en`, `es`, `fr`, `it`, `ja`, `nl`, `pl`, `pt-BR`, `sk`, `tr`, and `zh-CN`
- **Hourly regeneration + startup warmup** — Calendar and teams assets are regenerated on startup and every hour, with version metadata refreshed hourly and teams render assets warmed on boot
- **Any Timezone** — Convert race times to your local timezone
- **Race Status States** — Upcoming countdown, `IN PROGRESS` / `PROBÍHÁ`, `COMPLETED` / `DOKONČEN`, and cancelled race handling
- **Optional Weather Overlay** — Current, race-day forecast, and historical race-time weather on the calendar screen
- **Historical Results** — Previous year's podium for each circuit
- **Track Info** — Circuit map, length, laps, and first GP year
- **Display-Specific Track Art** — `1bit`, `bwr`, `bwry`, and `spectra6` now prefer per-display source artwork before falling back to generic circuit assets
- **Interactive configure flow** — Localized `/configure/calendar` and `/configure/teams` pages with pregenerated previews, direct BMP URLs, weather/display switching, and season leaders sidebar
- **SEO-friendly public pages** — Canonical URLs, hreflang alternates, `robots.txt`, and a localized `sitemap.xml` without synthetic daily `lastmod` churn
- **Session Schedule** — FP1, FP2, FP3, Qualifying, Sprint, Race times

### Roadmap

Planned features for future releases:

#### Display colors

- [x] **1-BIT monochrome** — Initial calendar output introduced in `v1.0.0`
- [x] **Spectra 6** — `display=spectra6` added in `v1.2.0`
- [x] **B/W/R** — `display=bwr` added in `v1.2.9`
- [x] **B/W/R/Y** — `display=bwry` added in `v1.2.13` for the calendar screen and extended to the teams screen in `v1.2.15`

#### Screens and layouts

- [x] **Championship standings** — Driver and constructor standings view
- [x] **Teams & Drivers screen** — Full team grid with driver photos and points
- [ ] **Custom layouts** — Multiple layout options to choose from
- [ ] **Additional display sizes** — Beyond 800x480 (e.g. 4.2", 5.83", 12.48")

#### Content and localization

- [ ] **More languages** — Additional community/localized translations beyond the current 13 supported locales
- [ ] **Extended weather integration** — Richer race weekend weather and extra weekend details
- [ ] **Dark mode variant** — Inverted colors for different display preferences

---

## ESP32 Integration

### Using zivyobraz.eu (Recommended)

Compatible with [zivyobraz.eu](https://zivyobraz.eu) — a service for managing ePaper displays with ESP32. See [documentation](https://wiki.zivyobraz.eu/doku.php?id=portal:url).

### Direct ESP32 Code

```cpp
#include <HTTPClient.h>

HTTPClient http;
http.begin("https://f1.inkycloud.click/calendar.bmp?lang=cs");
const char* responseHeaders[] = {"ETag"};
http.collectHeaders(responseHeaders, 1);

String etag = loadEtagFromPreferences();  // Persist across deep-sleep cycles.
if (!etag.isEmpty()) {
  http.addHeader("If-None-Match", etag);
}
int httpCode = http.GET();

if (httpCode == HTTP_CODE_OK) {
  saveEtagToPreferences(http.header("ETag"));
  display.drawBitmap(http.getStream());
} else if (httpCode == HTTP_CODE_NOT_MODIFIED) {
  // The BMP is unchanged: skip both the download and disruptive panel redraw.
}
```

Calendar and teams responses use a strong SHA-256 `ETag`. Send it back in `If-None-Match`; an
unchanged image returns `304 Not Modified` with an empty body. Persist the ETag in NVS/Preferences,
because an ESP32 commonly sleeps between polls. A changed race, weather view, language, timezone,
or renderer output returns `200` with a new ETag. Teams deliberately use `Cache-Control: no-cache`,
which permits conditional revalidation without accepting a stale response.

---

## Public Routes and API Endpoints

The public instance at [f1.inkycloud.click](https://f1.inkycloud.click) provides these endpoints:

| Endpoint                                 | Description                                             |
| ---------------------------------------- | ------------------------------------------------------- |
| `GET /calendar.bmp`                      | Calendar BMP with `lang`, `year`, `round`, `race_key`, `display`, `weather`, and `tz` params |
| `GET /teams.bmp`                         | Teams & drivers grid as BMP image (`lang`, `year`, `display`) |
| `GET /`                                  | Landing page with screen type selection                 |
| `GET /configure/{screen}`                | Interactive localized preview/config page (calendar/teams) |
| `GET /stats`                             | Public usage statistics dashboard                       |
| `GET /privacy`                           | Privacy policy page                                     |
| `GET /changelog`                         | Public changelog page                                   |
| `GET /preview/{screen}.png`              | Pre-generated localized homepage preview PNG            |
| `GET /preview/configure/{screen}.png`    | Pre-generated localized configure preview PNG           |
| `GET /api`                               | JSON API documentation                                  |
| `GET /api/docs`                          | Alias for `/api`                                        |
| `GET /api/docs/html`                     | Interactive HTML API docs                               |
| `GET /api/races/{year}`                  | All races for a season (JSON)                           |
| `GET /api/race/{year}/{round}`           | Specific race details (JSON)                            |
| `GET /api/teams/{year}`                  | Teams and drivers for a season (JSON)                   |
| `GET /api/standings/leader`              | Current championship leader (JSON)                      |
| `GET /api/standings/leader/{year}`       | Championship leader for a specific season (JSON)        |
| `GET /api/stats`                         | Request statistics, including 200/304 status totals     |
| `GET /api/stats/history`                 | Historical hourly request statistics                    |
| `POST /api/perf-metrics`                 | Store frontend performance metrics (Core Web Vitals)    |
| `GET /api/perf-metrics`                  | Read aggregated frontend performance metrics            |
| `GET /robots.txt`                        | Crawler policy with canonical sitemap reference         |
| `GET /sitemap.xml`                       | Localized sitemap with canonical URLs and hreflang alternates |
| `GET /sw.js`                             | Service worker script                                   |
| `GET /health`                            | Process liveness                                        |
| `GET /health/ready`                      | SQLite, storage, and core-generation readiness/degradation |

When `ADMIN_API_TOKEN` is configured, read-only operational endpoints (`/api/stats`, `/api/stats/history`, and `GET /api/perf-metrics`) require either `X-Admin-Token` or `Authorization: Bearer <token>`. Public image endpoints and `POST /api/perf-metrics` remain available, with rate limits applied.

---

## Self-Hosting

Production, CI, and local development use **Python 3.14**. Released containers are published to
GHCR with immutable version and commit tags, an SBOM, and provenance.

### Quick Docker Start

```bash
git clone https://github.com/Rhiz3K/InkyCloud-F1.git
cd InkyCloud-F1
cp .env.example .env
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z docker compose pull
F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:vX.Y.Z docker compose up -d --no-build
```

### Deployment Guides

- **[DEPLOYMENT.md](./DEPLOYMENT.md)** — choose the correct deployment path
- **[SELF-HOSTING.md](./SELF-HOSTING.md)** — Docker, operations, backups, and development
- **[COOLIFY.md](./COOLIFY.md)** — image-based Coolify deployment and rollback
- **[.env.example](./.env.example)** — canonical environment-variable reference
- **[scripts/README.md](./scripts/README.md)** — maintenance and unified asset CLI
- **[BMP_PROCESSING.md](./BMP_PROCESSING.md)** — source-art and runtime BMP pipeline

---

## License

See [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## Credits

- **Inspiration:** [FoxeeLab's original F1 E-Ink project](https://x.com/foxeelab/status/1761498129268981856)
- **Race data:** [Jolpica-F1 API](https://github.com/jolpica/jolpica-f1)
- **Weather:** [Open-Meteo](https://open-meteo.com)
- **Icons:** [Weather Icons](https://github.com/erikflowers/weather-icons) by Erik Flowers (SIL OFL 1.1)
- **Flags:** [Flagcdn](https://flagcdn.com)
- **Platform:** [Živýobraz.eu](https://zivyobraz.eu)
- **Devices:** [LaskaKit](https://www.laskakit.cz/) and [SVERIO](https://pajenicko.cz/sverio-paperboard-ctyrbarevny-7.5-gdem075f52-s-cernym-rameckem)
- **Hosting:** [Coolify](https://coolify.io) + [Hetzner](https://www.hetzner.com/)
- **Analytics:** [Umami](https://umami.is)
- **Errors:** [GlitchTip](https://glitchtip.com)
