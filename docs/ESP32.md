# ESP32 indoor sensor (optional)

Once you've got the brain running with weather-only input, you can add an ESP32 with a temperature sensor (DS18B20 or DHT22 are both fine) to provide indoor readings. When a fresh indoor reading is available, the brain uses it *in preference to* outdoor temp for its decision.

## 1. Enable the sensor in config

```yaml
sensor:
  enabled: true
  max_age_seconds: 600            # readings older than this are ignored
  indoor_threshold_celsius: 19.0  # turn heat on if indoor < this
  token: "pick-a-random-secret"   # optional; if set, ESP32 must send it
```

Restart the service: `sudo systemctl restart heating-brain`.

## 2. Example ESP32 sketch (Arduino IDE / PlatformIO)

Uses a **DS18B20** on GPIO 4. For DHT22, swap in the DHT library instead.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* WIFI_SSID     = "your-ssid";
const char* WIFI_PASSWORD = "your-password";

// Change this to your Pi's LAN IP
const char* BRAIN_URL = "http://192.168.1.50:8423/sensor";
const char* SENSOR_TOKEN = "pick-a-random-secret"; // must match config.yaml

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
  http.addHeader("X-Sensor-Token", SENSOR_TOKEN);

  String body = "{\"temperature_celsius\":";
  body += String(tempC, 2);
  body += "}";

  int code = http.POST(body);
  Serial.printf("POST %.2fC -> HTTP %d\n", tempC, code);
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

## 3. Verify

```bash
curl http://localhost:8423/status | python3 -m json.tool
```

You should see `indoor_temp_c` and a recent `indoor_fetched_at`. The next decision after that will use indoor temp instead of outdoor.

## Notes

- **If the ESP32 goes offline**, readings age out after `max_age_seconds` and the brain automatically falls back to outdoor-only logic. No manual intervention needed.
- **Placement matters** — put the sensor in a representative living area, not near radiators or exterior walls.
- **Multiple sensors** — not supported in this version. The last reading wins. If you want averaging across rooms, do it on the ESP32 side (or use one ESP32 per sensor and average them before POSTing).
