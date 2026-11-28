# Firmware integration

How a display fetches the image, revalidates it with `ETag` and sleeps until the next change.
Image parameters are described in [URL parameters](parameters.md).

## Using zivyobraz.eu (Recommended)

Compatible with [zivyobraz.eu](https://zivyobraz.eu) — a service for managing ePaper displays with ESP32. See [documentation](https://wiki.zivyobraz.eu/doku.php?id=portal:url).

## Direct ESP32 Code

```cpp
#include <HTTPClient.h>
#include <esp_sleep.h>

HTTPClient http;
// countdown=auto shows whole days until 48 h before the start, so the image (and ETag)
// changes about once a day instead of every hour. weather=false avoids hourly weather updates.
http.begin("https://racing.example.com/calendar.bmp?lang=cs&countdown=auto&weather=false");
const char* responseHeaders[] = {"ETag", "X-Next-Refresh-Seconds"};
http.collectHeaders(responseHeaders, 2);

String etag = loadEtagFromPreferences();  // Persist across deep-sleep cycles.
if (!etag.isEmpty()) {
  http.addHeader("If-None-Match", etag);
}
int httpCode = http.GET();

if (httpCode == HTTP_CODE_OK) {
  String newEtag = http.header("ETag");
  // drawBitmap stands for your display code; assume it returns false when the draw fails.
  // Save the ETag only after a successful draw, so a reset or failed draw cannot leave the
  // old image on the panel while the next poll returns 304.
  if (display.drawBitmap(http.getStream())) {
    saveEtagToPreferences(newEtag);
  }
} else if (httpCode == HTTP_CODE_NOT_MODIFIED) {
  // The BMP is unchanged: skip both the download and disruptive panel redraw.
}

// Sleep until the server expects the image to change (60-86400 s); fall back to one hour.
long sleepSeconds = http.header("X-Next-Refresh-Seconds").toInt();
if (sleepSeconds < 60 || sleepSeconds > 86400) sleepSeconds = 3600;
http.end();
esp_sleep_enable_timer_wakeup((uint64_t)sleepSeconds * 1000000ULL);
esp_deep_sleep_start();
```

Calendar and teams responses use a SHA-256 `ETag`: strong for uncompressed responses, and weak
(`W/"…"`) when the request accepts gzip and the body is compressed. Send it back unchanged in
`If-None-Match`; an unchanged image returns `304 Not Modified` with an empty body. Persist the ETag in NVS/Preferences,
because an ESP32 commonly sleeps between polls. A changed race, weather view, language, timezone,
or renderer output returns `200` with a new ETag. Teams deliberately use `Cache-Control: no-cache`,
which permits conditional revalidation without accepting a stale response.
The diagnostic `X-Cache` header reports where an image came from: `HIT` (in-process cache,
including a `304` for an image already in that cache), `PREGENERATED` (scheduler-written file
on disk), `REVALIDATED` (`304` checked against a pregenerated file's ETag without reading the
image), or `MISS` (rendered for this request).

Calendar and teams responses (`200` and `304`) also carry `X-Next-Refresh-Seconds`: the number
of seconds until the image is next expected to change, so a battery panel can pick its deep-sleep
length. It follows the next whole hour of a `d h` countdown, the next local midnight in the
requested `tz` for day counts (DST-aware), the race start and the end of the three-hour
`IN PROGRESS` window, the switch to the next race, and for teams the next sprint or race start
(hourly for 24 hours after one). The value is capped at `86400`, at `3600` while weather that is
refreshed hourly is shown (`weather_type=current`, or a race-day or weekend forecast inside the
16-day range), never goes below `60`, includes a two-minute margin for the hourly pre-generation, and is
`300` for an error image. It is a hint, not a promise: a data refresh can change the image
earlier, and a poll can still return `304` with a new hint.

The calendar `countdown` parameter controls how often the countdown itself changes the image:
`hours` (default, `12 d 17 h`, changes hourly), `days` (whole calendar days in `tz`, changes at
local midnight; `Today` on race day), `auto` (whole days until 48 hours before the start, then
days and hours) or `off` (the race date and start time instead of a countdown). Race statuses
(`IN PROGRESS`, `COMPLETED`, `CANCELLED`) and weather are shown the same way in every mode, and
an invalid value returns `422`. The teams `extra=countdown` panel already counts whole days.
