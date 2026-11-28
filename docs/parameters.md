# URL parameters

Image URLs (`/calendar.bmp`, `/teams.bmp`, `/auto.bmp`) accept the parameters below. The
configurator (`/configure/calendar`, `/configure/teams`) builds the same URLs. Ready-to-run
`curl` commands are in [EXAMPLES.md](../EXAMPLES.md), all endpoints in [API](api.md) and
firmware polling in [Firmware integration](firmware.md).

## Parameter table

| Parameter      | Options                          | Example                              |
| -------------- | -------------------------------- | ------------------------------------ |
| `lang`         | `cs`, `de`, `en`, `es`, `fr`, `it`, `ja`, `nl`, `pl`, `pt-BR`, `sk`, `tr`, `zh-CN` | `?lang=sk` |
| `tz`           | Any IANA timezone                | `?tz=America/New_York`               |
| `year`         | Season year; pair with `round` or `race_key` to select a race | `?year=2026&round=6` |
| `round`        | Race round number                | `?year=2026&round=5`                 |
| `race_key`     | Specific race key from `/api/races/{year}` (requires `year`) | `?year=2026&race_key=2026-round-6-monaco-2026-06-07` |
| `display`      | `1bit`, `bwr`, `bwry`, `spectra6` | `?display=bwry`                      |
| `weather`      | `true`, `false`                  | `?weather=false`                     |
| `weather_type` | `race_day`, `race`, `current`, `weekend`, `off` — see [Weekend weather](#weekend-weather-and-fahrenheit) | `?weather=true&weather_type=current` |
| `temp_unit`    | `c` (default), `f` — Fahrenheit, also for the teams weather panel | `?temp_unit=f` |
| `countdown`    | `hours` (default), `days`, `auto`, `off` | `?countdown=auto` |
| `weekend_mode` | `off` (default), `auto` — follow the race weekend, see [Race weekend mode](#race-weekend-mode-weekend_modeauto) | `?weekend_mode=auto` |
| `track_style`  | 18 style identifiers from `/api/tracks`; default `relief` | `?track_style=contours` |
| `track_source` | `jules` (default), `commons`       | `?track_source=commons`              |
| `track_accent` | 16 accent subsets from `/api/tracks`; default `all` | `?track_accent=warm` |
| `format`       | `bmp` (default), `png` — palette-exact PNG, see [PNG output and rotation](#png-output-and-rotation) | `?format=png` |
| `rotate`       | `0` (default), `180` — upside-down panel | `?rotate=180` |
| `theme`        | `light` (default), `dark` — experimental, see [Dark theme](#dark-theme-themedark) | `?theme=dark` |

**Examples:**

```text
https://racing.example.com/calendar.bmp?lang=cs
https://racing.example.com/calendar.bmp?lang=en&tz=America/New_York
https://racing.example.com/calendar.bmp?lang=en&year=2026&round=5
https://racing.example.com/calendar.bmp?lang=en&year=2026&race_key=2026-round-6-monaco-2026-06-07
https://racing.example.com/calendar.bmp?lang=en&display=bwr
https://racing.example.com/calendar.bmp?lang=en&display=bwry
https://racing.example.com/calendar.bmp?lang=en&display=spectra6
https://racing.example.com/calendar.bmp?lang=en&weather=true&weather_type=current
https://racing.example.com/calendar.bmp?lang=en&countdown=auto&weather=false
https://racing.example.com/calendar.bmp?lang=en&weekend_mode=auto&display=bwry
https://racing.example.com/calendar.bmp?lang=en&tz=America/New_York&weather_type=weekend&temp_unit=f
https://racing.example.com/calendar.bmp?lang=en&display=spectra6&format=png&rotate=180
https://racing.example.com/calendar.bmp?lang=en&display=bwr&theme=dark
https://racing.example.com/teams.bmp?lang=ja&display=spectra6
https://racing.example.com/auto.bmp?lang=de&tz=Europe/Berlin&display=bwry
https://racing.example.com/sk/configure/calendar
```

## Race weekend mode (`weekend_mode=auto`)

With `weekend_mode=auto` the calendar follows the race weekend, from 12 hours before the first
session until the next race is selected (4 hours after the race start). Outside that window the
image is the same as without the parameter.

- Finished sessions get a check mark and a plain name; the running or next session gets a
  marker. Both marks sit in the narrow gap left of the schedule dates, in every palette.
- The status box counts down to the next session with its short name (`Q: 3 h`, `SPRINT: 19 h`,
  `Q: LIVE` while it runs) in the chosen `countdown` mode. When the race is next, the box shows
  the race countdown and status as usual.
- A session counts as finished from its start plus a conservative estimate of its length
  (practice 75 min, sprint qualifying and sprint 60 min, qualifying 90 min, race 3 h). The
  real end time is not published, so a session can be shown as running a little longer.
- After qualifying and after the race the footer shows this weekend's top three (for example
  `2026 QUALIFYING` next to `2025 RACE`) once [Jolpica](https://jolpi.ca) publishes them. The
  hourly job asks Jolpica only in the 12 hours after those two sessions, at most once an hour
  and only until a complete top three is stored (about 2–6 requests per weekend). The default
  calendar keeps last year's results.
- `X-Next-Refresh-Seconds` also wakes the display at each session start and estimated end,
  and at least hourly while new results may arrive. Invalid values return `422`. It also works
  on `/auto.bmp` and in the configurator ("Race weekend").

## Weekend weather and Fahrenheit

- `weather_type=weekend` adds a second line to the status box with the forecast for qualifying
  and the race, e.g. `Q ☀ 19° 💧60% | R ☀ 22° 💧10%`, and the sprint too when it fits. One
  [Open-Meteo](https://open-meteo.com) request covers the whole weekend (the race day and the
  two days before it); it is the same request `race_day` makes, so both share the data and
  the request still counts as one call in the shared quota. Sessions beyond the 16-day forecast,
  already past or without a published start are left out, and after the race starts only the
  race weather is shown. The countdown and session labels keep the first line.
- `temp_unit=f` draws temperatures in Fahrenheit (`round(°C × 9/5 + 32)`) on the calendar, the
  teams weather panel (`extra=weather`), `/auto.bmp` and the configure previews. The weather
  data stays in Celsius, so Fahrenheit costs no extra weather requests. `c` (default) keeps the
  previous images byte for byte. The configurator suggests °F for browsers in US time zones.
- Both are rendered on demand and cached in the variant cache. Self-hosters can pregenerate the
  weekend calendars with `PREGENERATE_WEATHER_TYPES=...,weekend`. Invalid `temp_unit` values
  return `422`.

## PNG output and rotation

`format=png` and `rotate=180` work on `/calendar.bmp`, `/teams.bmp` and `/auto.bmp`, together
with every other parameter.

- `format=png` returns a lossless PNG with exactly the same pixels as the BMP: 1-bit greyscale
  for `1bit`, and an indexed PNG whose palette is exactly the panel palette for `bwr`, `bwry`
  and `spectra6` (no conversion to greyscale or RGB, no dithering, no extra colours, so every
  pixel keeps its palette index). It is much smaller to download, which saves radio time and
  RAM on a battery-powered ESP32, but your firmware must be able to decode PNG; keep the default
  BMP otherwise. Measured on an 800×480 calendar:

  | `display`  | BMP       | PNG      | Smaller |
  | ---------- | --------- | -------- | ------- |
  | `1bit`     | 48 062 B  | 10 507 B | 4.6×    |
  | `bwr`      | 192 118 B | 12 842 B | 15×     |
  | `bwry`     | 192 118 B | 13 017 B | 15×     |
  | `spectra6` | 385 078 B | 14 340 B | 27×     |

- `rotate=180` turns the image upside down without loss for a panel mounted with its connector
  on the other side. The image stays 800×480 and a rotated BMP keeps the default bit depth and
  header (1-bit, 4-bit for `bwr`/`bwry`, 8-bit for `spectra6`), so existing BMP parsers keep
  working. `90` and `270` are not offered: they would need a separate portrait layout.
- The path stays `.bmp` even for PNG; `Content-Type` (`image/png`), `Content-Length`, the
  `Content-Disposition` file name and the `ETag` describe the bytes actually sent, so every
  format and rotation revalidates with its own `304`. Licence and `X-Next-Refresh-Seconds`
  headers are the same as for the BMP.
- The default (`format=bmp&rotate=0`) is byte-identical to a URL without these parameters.
  Other variants are converted from that same default image (in-process cache or pregenerated
  file) once on the render workers and kept in the variant cache. Invalid values return `422`.
  The configurator offers both under "Image output"; its preview always stays upright.

## Dark theme (`theme=dark`)

`theme=dark` (experimental) works on `/calendar.bmp`, `/teams.bmp`, `/auto.bmp` and the
configurator previews, together with every other parameter (`format=png`, `rotate=180`,
`weekend_mode`, `weather_type=weekend`, `temp_unit`, `countdown`, `extra`, `track_*`).

- It is not a pixel inversion. Black and white swap roles (black background, white text, lines
  and outlines), while red, yellow and the Spectra 6 green and blue keep their roles, so a red
  header, countdown or P1 badge stays red on `bwr`, `bwry` and `spectra6`.
- Country flags keep their own colours and get a thin white border; the header car is drawn as
  white line art. Every one of the 18 track styles swaps its black and white inks (colour bands
  stay); `negative` already draws on black and is unchanged. On the 1-bit panel the black title
  band is underlined in white and the countdown becomes a white box with black text.
- Text keeps the hard edges or palette mapping of each display, so dark images contain only
  the panel's own colours.
- Large black areas can leave ghosting on some e-ink panels (especially with partial refresh).
  Prefer a full refresh, and check the result on your own hardware before relying on it.
- `light` (the default) is byte-identical to a URL without the parameter. Dark images are
  rendered on demand into the variant cache with their own cache keys and ETags; they are never
  pregenerated, so they do not add to the hourly scheduler work. Invalid values return `422`.

## One URL for Both Screens (`/auto.bmp`)

`GET /auto.bmp` returns the calendar or the teams BMP directly (no redirect), so a display that
can poll only one address still gets both screens. It accepts the calendar and teams parameters
above (`lang`, `tz`, `display`, `weather`, `weather_type`, `countdown`, `weekend_mode`,
`temp_unit`, `track_*`, `extra`, `year`, `format`, `rotate`, `theme`); each applies to its own screen. `year` selects the teams season; the calendar always
shows the next race.

| Parameter    | Options                              | Example            |
| ------------ | ------------------------------------ | ------------------ |
| `mode`       | `race_week` (default), `daily`       | `?mode=daily`      |
| `lead_hours` | `0`–`168` (default `48`)             | `?lead_hours=72`   |
| `extra`      | Teams panel; default `countdown` here | `?extra=none`     |

- `race_week` shows the calendar from `lead_hours` before the first session (FP1) of the next
  race weekend until the next race is selected (4 hours after the start; for a race whose
  start time is not published yet, the end of the local race day in `tz`). Otherwise it shows
  the teams with the countdown panel, also in the off-season and around cancelled races.
- `daily` alternates by the local date in `tz` (calendar on even, teams on odd days of
  `date.toordinal()`) and switches at local midnight.
- `X-Active-Screen: calendar|teams` names the returned screen. The bytes, `ETag`, `Link` licence
  headers and cache entries are the same as on the direct URL with the same parameters, so a
  `304` is only returned while the display already shows the active screen.
  `X-Next-Refresh-Seconds` and the `Cache-Control` `max-age` never reach past the next switch.
- Invalid `mode` or `lead_hours` values return `422`. The configurator offers the same URL under
  "One URL for both screens".
