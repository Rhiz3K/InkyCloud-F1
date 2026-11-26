# F1 E-Ink Calendar - API Examples

## Basic Usage

### Fetch Calendar BMP

```bash
# English version
curl http://localhost:8000/calendar.bmp?lang=en -o calendar.bmp

# Czech version
curl http://localhost:8000/calendar.bmp?lang=cs -o calendar.bmp
```

### Battery-Friendly Countdown and Refresh Hint

```bash
# Whole days only: the image changes once a day at local midnight (plus race status changes)
curl "http://localhost:8000/calendar.bmp?countdown=days&weather=false" -o calendar.bmp

# Days until 48 hours before the start, then days and hours; show the refresh hint
curl -sD - "http://localhost:8000/calendar.bmp?countdown=auto&weather=false" -o calendar.bmp \
  | grep -i x-next-refresh-seconds

# Race date instead of a countdown
curl "http://localhost:8000/calendar.bmp?countdown=off" -o calendar.bmp
```

### Race Weekend Mode

```bash
# During the race weekend: finished sessions ticked, next session marked and counted down,
# this weekend's qualifying and race podiums once Jolpica publishes them
curl "http://localhost:8000/calendar.bmp?weekend_mode=auto&display=bwry" -o calendar.bmp

# Combine with whole-day countdowns and the one-URL screen switch
curl "http://localhost:8000/auto.bmp?weekend_mode=auto&countdown=auto&tz=Europe/Prague" \
  -o screen.bmp
```

### Weekend Weather and Fahrenheit

```bash
# Qualifying (and sprint when it fits) plus race forecast from one Open-Meteo request
curl "http://localhost:8000/calendar.bmp?weather_type=weekend&display=bwry" -o calendar.bmp

# The same in Fahrenheit for a US display, and the teams weather panel in Fahrenheit
curl "http://localhost:8000/calendar.bmp?weather_type=weekend&temp_unit=f&tz=America/New_York" \
  -o calendar.bmp
curl "http://localhost:8000/teams.bmp?extra=weather&temp_unit=f" -o teams.bmp
```

### One URL for Both Screens

```bash
# Calendar in race week (from 48 h before FP1), teams with a countdown otherwise.
# Use this single URL for Živýobraz.eu or firmware that polls one address.
curl -sD - "http://localhost:8000/auto.bmp?lang=cs&tz=Europe/Prague" -o screen.bmp \
  | grep -i -E "x-active-screen|x-next-refresh-seconds"

# Show the calendar three days ahead and the plain teams grid otherwise
curl "http://localhost:8000/auto.bmp?lead_hours=72&extra=none" -o screen.bmp

# Alternate calendar and teams every day at local midnight
curl "http://localhost:8000/auto.bmp?mode=daily&tz=America/New_York&display=bwry" -o screen.bmp
```

### PNG Output and 180° Rotation

```bash
# Palette-exact PNG: same pixels and panel palette as the BMP, about 14 KB instead of 385 KB
curl -sD - "http://localhost:8000/calendar.bmp?display=spectra6&format=png" -o calendar.png \
  | grep -i -E "content-type|content-length|etag"

# Upside down for an enclosure with the connector on the other side (still a 4-bit BMP)
curl "http://localhost:8000/teams.bmp?display=bwr&rotate=180" -o teams.bmp

# Both, on the one-URL screen switch (the path stays .bmp; Content-Type is image/png)
curl "http://localhost:8000/auto.bmp?display=bwry&format=png&rotate=180" -o screen.png
```

| `display`  | BMP       | `format=png` |
| ---------- | --------- | ------------ |
| `1bit`     | 48 062 B  | ~10.5 KB     |
| `bwr`      | 192 118 B | ~12.8 KB     |
| `bwry`     | 192 118 B | ~13.0 KB     |
| `spectra6` | 385 078 B | ~14.3 KB     |

Measured on an 800×480 calendar. `90` and `270` are not offered because they need a portrait
layout; invalid `format` or `rotate` values return `422`.

### Dark Theme

```bash
# Experimental dark theme: black background, white text, red accents stay red
curl "http://localhost:8000/calendar.bmp?display=bwr&theme=dark" -o calendar.bmp

# Works with every other parameter, here the teams flags panel as a rotated PNG
curl "http://localhost:8000/teams.bmp?display=spectra6&extra=flags&theme=dark&format=png&rotate=180" \
  -o teams.png

# And on the one-URL screen switch
curl "http://localhost:8000/auto.bmp?display=1bit&theme=dark" -o screen.bmp
```

`theme=light` (the default) returns the same bytes as a URL without `theme`; an invalid value
returns `422`. Large black areas can ghost on some panels, so prefer a full refresh.

### Check Health

```bash
curl http://localhost:8000/health

# Readiness of the database, storage, and generated images
curl http://localhost:8000/health/ready
```

### Get Landing Page / API Info

```bash
# Landing page
curl http://localhost:8000/

# JSON API documentation
curl http://localhost:8000/api
```

### Fetch with Timezone

```bash
# Prague timezone (default)
curl "http://localhost:8000/calendar.bmp?lang=cs&tz=Europe/Prague" -o calendar.bmp

# New York timezone
curl "http://localhost:8000/calendar.bmp?lang=en&tz=America/New_York" -o calendar.bmp

# Tokyo timezone
curl "http://localhost:8000/calendar.bmp?lang=en&tz=Asia/Tokyo" -o calendar.bmp

# Sydney timezone
curl "http://localhost:8000/calendar.bmp?lang=en&tz=Australia/Sydney" -o calendar.bmp
```

### Privacy Policy

```bash
# English privacy policy
curl http://localhost:8000/privacy

# Czech privacy policy
curl http://localhost:8000/cs/privacy
```

### API Statistics

```bash
# Get 24h request totals, response status counts, and artwork usage
curl http://localhost:8000/api/stats

# Get historical stats (last 168 hours by default)
curl http://localhost:8000/api/stats/history

# Get historical stats with custom limit (max 720 hours = 30 days)
curl "http://localhost:8000/api/stats/history?limit=720"

# Get aggregate performance metrics (24 hours by default; maximum 720)
curl "http://localhost:8000/api/perf-metrics?hours=24"
```

These endpoints return HTTP 410 when `MINIMAL_DATA_MODE=true`. When
`ADMIN_API_TOKEN` is configured, statistics reads also require
the `X-Admin-Token` header or the `Authorization` header with the `Bearer` scheme.

### Select a Race

```bash
# List season races and their race_key values
curl http://localhost:8000/api/races/2026

# Select Monaco using its key from the bundled 2026 season
SELECTED_RACE=2026-round-6-monaco-2026-06-07
curl "http://localhost:8000/calendar.bmp?year=2026&race_key=$SELECTED_RACE" -o monaco.bmp

# Select a round instead
curl "http://localhost:8000/calendar.bmp?year=2026&round=6" -o round_6.bmp
```

`race_key` requires `year`. Without `round` or `race_key`, the calendar shows the
automatically selected next race, even if `year` is supplied.

### Circuit Artwork

```bash
# Discover styles, source families, accent subsets, and circuit IDs
curl http://localhost:8000/api/tracks

# Calendar with custom artwork and all six display colours
curl "http://localhost:8000/calendar.bmp?display=spectra6&track_style=contours&track_source=commons&track_accent=all" -o calendar_colour.bmp

# Standalone export (defaults to Spectra 6)
curl "http://localhost:8000/api/tracks/monza.svg?track_style=relief" -o monza.svg

# Per-file authorship and licences
curl http://localhost:8000/api/tracks/monza
```

### Championship Standings

```bash
# Get current championship leaders (JSON)
curl http://localhost:8000/api/standings/leader

# Get championship leaders for a specific year
curl http://localhost:8000/api/standings/leader/2025
```

### Teams & Drivers BMP

```bash
# Current season teams screen
curl http://localhost:8000/teams.bmp -o teams.bmp

# Specific season
curl "http://localhost:8000/teams.bmp?year=2025" -o teams_2025.bmp

# Czech language
curl "http://localhost:8000/teams.bmp?lang=cs" -o teams_cs.bmp

# Display variants
curl "http://localhost:8000/teams.bmp?display=bwr" -o teams_bwr.bmp
curl "http://localhost:8000/teams.bmp?display=bwry" -o teams_bwry.bmp
curl "http://localhost:8000/teams.bmp?display=spectra6" -o teams_spectra6.bmp
```

### Interactive API Documentation

```bash
# HTML documentation page
curl http://localhost:8000/api/docs/html

# JSON API info
curl http://localhost:8000/api
```

## ESP32 Integration Example

```cpp
#include <HTTPClient.h>
#include <WiFi.h>

// Define WIFI_SSID / WIFI_PASSWORD in secrets.h and keep that file out of version control.
#include "secrets.h"

const char* serverUrl = "http://your-server:8000/calendar.bmp?lang=cs";

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected to WiFi");
  fetchAndDisplayCalendar();
}

void fetchAndDisplayCalendar() {
  HTTPClient http;
  http.begin(serverUrl);

  int httpCode = http.GET();

  if (httpCode == HTTP_CODE_OK) {
    int len = http.getSize();
    WiFiClient *stream = http.getStreamPtr();

    // Read BMP header and data
    uint8_t buff[128] = { 0 };
    while (http.connected() && (len > 0 || len == -1)) {
      size_t size = stream->available();
      if (size) {
        int c = stream->readBytes(buff, ((size > sizeof(buff)) ? sizeof(buff) : size));
        // Process BMP data and display on E-Ink
        // displayBitmap(buff, c);
        if (len > 0) len -= c;
      }
      delay(1);
    }
    Serial.println("Calendar downloaded successfully");
  } else {
    Serial.printf("HTTP GET failed, error: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}

void loop() {
  // Update every hour
  delay(3600000);
  fetchAndDisplayCalendar();
}
```

## Python Client Example

```python
import requests
from PIL import Image
from io import BytesIO

def fetch_calendar(lang="en"):
    """Fetch and display F1 calendar."""
    url = f"http://localhost:8000/calendar.bmp?lang={lang}"
    response = requests.get(url)

    if response.status_code == 200:
        # Save BMP
        with open(f"calendar_{lang}.bmp", "wb") as f:
            f.write(response.content)

        # Display with Pillow
        img = Image.open(BytesIO(response.content))
        img.show()
    else:
        print(f"Error: {response.status_code}")

# Fetch English calendar
fetch_calendar("en")

# Fetch Czech calendar
fetch_calendar("cs")
```

## Testing with curl

```bash
# Test all endpoints
curl http://localhost:8000/
curl http://localhost:8000/health
curl http://localhost:8000/calendar.bmp?lang=en -o test.bmp
curl http://localhost:8000/teams.bmp -o teams_test.bmp

# Verify BMP format
file test.bmp
# Expected: PC bitmap, Windows 3.x format, 800 x 480 x 1

# Check image dimensions with ImageMagick
identify test.bmp
```

## Docker Deployment

```bash
# Build
docker build -t f1-eink-cal .

# Run with environment variables
docker run -d \
  -p 8000:8000 \
  -e SENTRY_DSN=your-sentry-dsn \
  --name f1-calendar \
  f1-eink-cal

# Check logs
docker logs -f f1-calendar

# Stop
docker stop f1-calendar
```

## Development

```bash
# Run in debug mode
DEBUG=true uvicorn app.main:app --reload

# Test with different languages
curl http://localhost:8000/calendar.bmp?lang=cs -o calendar_cs.bmp
curl http://localhost:8000/calendar.bmp?lang=en -o calendar_en.bmp

# Convert BMP to PNG for viewing
convert calendar.bmp calendar.png
# or with Python
python -c "from PIL import Image; Image.open('calendar.bmp').save('calendar.png')"
```

### Preprocess flag assets

The monochrome flag preprocessor depends on the locked development group (NumPy and
scikit-learn):

```bash
uv sync --locked --group dev
uv run python -m scripts.manage preprocess flags --palette mono
```
