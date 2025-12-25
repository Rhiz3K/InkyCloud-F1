# F1 E-Ink Calendar - API Examples

## Basic Usage

### Fetch Calendar BMP

```bash
# English version
curl http://localhost:8000/calendar.bmp?lang=en -o calendar.bmp

# Czech version
curl http://localhost:8000/calendar.bmp?lang=cs -o calendar.bmp
```

### Check Health

```bash
curl http://localhost:8000/health
```

### Get API Info

```bash
curl http://localhost:8000/
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
curl http://localhost:8000/privacy?lang=en

# Czech privacy policy
curl http://localhost:8000/privacy?lang=cs
```

### API Statistics

```bash
# Get current stats (1h and 24h request counts)
curl http://localhost:8000/api/stats

# Get historical stats (last 168 hours by default)
curl http://localhost:8000/api/stats/history

# Get historical stats with custom limit (max 720 hours = 30 days)
curl "http://localhost:8000/api/stats/history?limit=720"
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

const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
const char* serverUrl = "http://your-server:8000/calendar.bmp?lang=cs";

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  
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
  -e UMAMI_WEBSITE_ID=your-umami-id \
  -e UMAMI_ENABLED=true \
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

The flag preprocessing utility depends on optional packages (NumPy, scikit-learn). Install the
development extras before running it:

```bash
pip install -e .[dev]
python scripts/preprocess_flags.py
```
