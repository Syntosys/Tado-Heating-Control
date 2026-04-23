# ESP32 temperature sensors (optional)

Any number of ESP32s (or other devices) can POST temperature readings to `/sensor`. Each reading declares its own **location** (`indoor` or `outdoor`) and an optional **sensor_id** so you can have several sensors per location. When fresh readings are available, the brain uses them in preference to the fallback sources (Tado's built-in zone temperature for indoor; Open-Meteo for outdoor).

## 1. Enable sensors in config

Edit `/etc/heating-brain/config.yaml`:

```yaml
sensor:
  # Turn these on independently. Either or both can be enabled.
  indoor_enabled: true
  outdoor_enabled: false

  # Readings older than this many seconds are ignored.
  max_age_seconds: 600

  # How to combine multiple readings at the same location.
  # Options: mean (default), min, max
  indoor_aggregate: mean
  outdoor_aggregate: mean

  # Optional shared secret. If set, every ESP32 must include it.
  # token: "pick-a-random-secret"
```

Restart the service: `sudo systemctl restart heating-brain`.

## 2. POST format

`POST http://<pi-ip>:8423/sensor` with JSON body:

```json
{
  "temperature_celsius": 20.3,
  "location": "indoor",
  "sensor_id": "living-room"
}
```

Fields:

| Field | Required | Default | Notes |
|---|---|---|---|
| `temperature_celsius` | yes | — | Floating-point °C, range −40 to 80 |
| `location` | no | `"indoor"` | `"indoor"` or `"outdoor"` |
| `sensor_id` | no | `"default"` | Any string. Readings with the same id overwrite each other. |

If a `sensor.token` is configured, include the header `X-Sensor-Token: <that-secret>`.

Older ESP32 sketches that POST only `{"temperature_celsius": N}` keep working — they're treated as a single indoor sensor with id `default`.

## 3. Example ESP32 sketch (Arduino IDE / PlatformIO)

Uses a **DS18B20** on GPIO 4. For a DHT22, swap in the DHT library instead.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* WIFI_SSID     = "your-ssid";
const char* WIFI_PASSWORD = "your-password";

// Pi's LAN IP
const char* BRAIN_URL = "http://192.168.1.50:8423/sensor";
const char* SENSOR_TOKEN = "pick-a-random-secret"; // must match config.yaml, or "" to skip

// Identity of this sensor
const char* SENSOR_ID = "living-room";
const char* LOCATION  = "indoor";  // or "outdoor"

const int ONE_WIRE_PIN = 4;
OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature sensors(&oneWire);

const unsigned long POST_INTERVAL_MS = 30 * 1000; // 30s

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  sensors.begin();
  connectWifi();
}

void postTemperature(float tempC) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  HTTPClient http;
  http.begin(BRAIN_URL);
  http.addHeader("Content-Type", "application/json");
  if (strlen(SENSOR_TOKEN) > 0) {
    http.addHeader("X-Sensor-Token", SENSOR_TOKEN);
  }

  String body = "{\"temperature_celsius\":";
  body += String(tempC, 2);
  body += ",\"location\":\"";
  body += LOCATION;
  body += "\",\"sensor_id\":\"";
  body += SENSOR_ID;
  body += "\"}";

  int code = http.POST(body);
  Serial.printf("POST %s@%s %.2fC -> HTTP %d\n", SENSOR_ID, LOCATION, tempC, code);
  http.end();
}

void loop() {
  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);
  if (t > -50 && t < 80) {  // DEVICE_DISCONNECTED_C is -127
    postTemperature(t);
  } else {
    Serial.println("Bad reading, skipping");
  }
  delay(POST_INTERVAL_MS);
}
```

Flash one sketch per ESP32. Change `SENSOR_ID` (and `LOCATION` where relevant) per device.

## 4. Verify

```bash
curl -s http://<pi-ip>:8423/status | python3 -m json.tool
```

Look for:

- `indoor_source` / `outdoor_source` — shows `"sensor"` when a fresh ESP32 reading drove the decision, `"tado"` or `"weather"` when falling back
- `sensors` — an object keyed by `sensor_id` listing every recent reading with its location, value, and age

Next decision after a fresh reading should use the ESP32 value; you'll see the active temp and source reflected in the web UI and MagicMirror tile.

## Curl test (no ESP32 needed)

```bash
curl -X POST http://<pi-ip>:8423/sensor \
     -H "Content-Type: application/json" \
     -H "X-Sensor-Token: pick-a-random-secret" \
     -d '{"temperature_celsius": 19.5, "location": "indoor", "sensor_id": "test"}'
```

Expect: `{"ok":true,"sensor_id":"test","location":"indoor"}`

## Notes

- **If an ESP32 goes offline**, its reading ages out after `max_age_seconds`. If all sensors at a location are stale, the brain falls back automatically — no manual intervention.
- **Placement matters** — indoor sensors in representative living areas (not next to radiators or exterior walls). Outdoor sensors in shade (direct sun skews readings hard).
- **Multiple sensors per location** — fully supported. Combined per the `indoor_aggregate` / `outdoor_aggregate` setting (`mean`, `min`, or `max`).
- **Mixing sources** — you can run an indoor ESP32 while leaving outdoor to Open-Meteo, or vice-versa. The two flags are independent.
