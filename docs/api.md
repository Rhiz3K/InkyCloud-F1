# Public routes and API endpoints

Your deployed instance provides these endpoints:

| Endpoint                                 | Description                                             |
| ---------------------------------------- | ------------------------------------------------------- |
| `GET /calendar.bmp`                      | Calendar BMP with `lang`, `year`, `round`, `race_key`, `display`, `weather`, `weather_type`, `countdown`, `weekend_mode`, `temp_unit`, `tz`, `track_style`, `track_source`, and `track_accent` params; `format=png`, `rotate=180` and `theme=dark` on all three image endpoints; `X-Next-Refresh-Seconds` hint header |
| `GET /teams.bmp`                         | Teams & drivers grid as BMP image (`lang`, `year`, `display`, `extra`: `none`, `countdown`, `flags`, `track`, `weather`; `temp_unit` for the weather panel); optional IANA `tz` applies only to countdown, flags, track, and weather panels showing a time or date (also on `/preview/configure/teams.png`); attribution/licences at `/credits` (`Link rel="license"` header) |
| `GET /auto.bmp`                          | Calendar or teams BMP on one URL (`mode`: `race_week`, `daily`; `lead_hours` 0–168; calendar and teams params); `X-Active-Screen` header |
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
| `GET /api/tracks`                        | Circuit artwork catalogue, available styles, sources, accents, and display palettes |
| `GET /api/tracks/{circuit_id}`           | Circuit artwork metadata and per-file attribution (JSON) |
| `GET /api/tracks/{circuit_id}.{svg\|png\|bmp}` | Attributed circuit artwork export; default display `spectra6` |
| `GET /credits`                           | Circuit artwork authors and licences                    |
| `GET /api/stats`                         | Request statistics, 200/304 status totals and map style/source/accent usage |
| `GET /api/stats/history`                 | Historical hourly request statistics                    |
| `POST /api/perf-metrics`                 | Submit numeric performance measurements; 410 in minimal mode |
| `GET /api/perf-metrics`                  | Performance summaries; 410 only in minimal mode            |
| `GET /robots.txt`                        | Crawler policy with canonical sitemap reference         |
| `GET /sitemap.xml`                       | Localized sitemap with canonical URLs and hreflang alternates |
| `GET /sw.js`                             | Service worker script                                   |
| `GET /health`                            | Process liveness                                        |
| `GET /health/ready`                      | SQLite, storage, and core-generation readiness/degradation |
| `GET /api/ops/status`                    | Operator diagnostics with reason codes; only with `ADMIN_API_TOKEN` (bearer), otherwise 404 |

Statistics and their read APIs are available by default with `MINIMAL_DATA_MODE=false`
and `AGGREGATE_STATS_ONLY=true`. They store hourly usage totals and coarse performance
histograms without visitor identifiers. `POST /api/perf-metrics` accepts numeric measurements
only; a random 10% sample of page loads sends one report. When `ADMIN_API_TOKEN` is configured,
read-only statistics APIs require `X-Admin-Token` or `Authorization: Bearer <token>`.
Explicit `MINIMAL_DATA_MODE=true` makes the statistics page and all statistics APIs return 410.

Statistics storage and retention are described in [data collection](data-collection.md).
Image parameters are in [URL parameters](parameters.md) and `curl` examples in
[EXAMPLES.md](../EXAMPLES.md). A running instance also serves interactive docs at
`/api/docs/html`.
