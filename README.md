# Tado-Heating-Control

A schedule- and temperature-driven controller for [tado°](https://www.tado.com) heating, designed to run on a Raspberry Pi — optionally alongside [MagicMirror²](https://magicmirror.builders).

> The code, package, and systemd service are internally named `heating-brain` for brevity — that's just the running process. This repo is the thing you clone.

It checks outdoor temperature (from a weather API, with optional indoor sensor input from an ESP32) against a schedule of time windows, each with its own threshold, and turns your Tado heating on or off accordingly. Hysteresis and a minimum-state-change interval keep it from rapid-cycling.

## Why this exists

Tado's own scheduling is fine, but it doesn't let you say "only heat in the mornings if it's below 12°C outside." This does.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Raspberry Pi                                       │
│                                                     │
│  ┌──────────────────────┐   ┌───────────────────┐   │
│  │  heating-brain       │   │  MagicMirror²     │   │
│  │  (systemd service)   │◄──┤  + MMM-HeatingBrain│  │
│  │                      │   │  (polls /status)  │   │
│  │  • Open-Meteo poll   │   └───────────────────┘   │
│  │  • Schedule engine   │                           │
│  │  • Tado device-flow  │   ┌───────────────────┐   │
│  │  • HTTP API :8423    │◄──┤  ESP32 (optional) │   │
│  └──────────────────────┘   │  POSTs /sensor    │   │
│                             └───────────────────┘   │
└─────────────────────────────────────────────────────┘
```

The brain runs headless and keeps working even if MagicMirror crashes or the screen sleeps. The mirror module is a thin status viewer.

## Tado API notes — read this first

Tado made significant changes in 2025 that affect anyone integrating with their API:

- **Mandatory OAuth2 device code flow** as of 21 March 2025. Username/password is dead. First run opens a URL you visit in a browser to link the app to your account.
- **Rate limits:** 100 API calls/day on the free tier, 20,000/day with an [Auto-Assist](https://www.tado.com) subscription. This project's default 60-second poll interval uses ~1,440 calls/day — fine for Auto-Assist, **too many for the free tier**. If you're on the free tier, bump `tado.poll_interval_seconds` to `900` (15 minutes) to stay under 100/day.

## Features

- OAuth2 device code flow (the new mandatory auth method)
- Auto-refreshing access tokens with persistent refresh token
- Multiple scheduled windows with per-window thresholds
- Hysteresis to prevent rapid on/off cycling
- Configurable minimum time between state changes
- Optional indoor sensor input via HTTP (ESP32-ready)
- HTTP API for dashboards and integrations
- **Mobile web UI** — PIN-protected multi-page SPA served at port 8423; four tabs: Now (live status + On/Off/Auto controls), History (24 h and 7-day temperature/heating charts), Schedule (add/edit/delete windows), Settings (version/update + PIN change)
- **Multi-device support** — run one Pi in `mode: primary` and any number of other devices in `mode: client`; the clients host the same web UI but proxy every request to the primary, so the Tado API is only ever polled once
- **External-change detection** — if Alexa, the Tado app, or anyone else flips the heating, the brain notices next tick, adopts the new state, and treats it as a manual override until the next schedule-window transition (no extra API calls — it piggybacks on the zone-state fetch already used for indoor temperature)
- MagicMirror² module included
- Systemd hardening (runs as unprivileged user, read-only filesystem)

## Quickstart

See [docs/INSTALL.md](docs/INSTALL.md) for the full setup guide. Short version:

```bash
# On the Pi
git clone https://github.com/YOUR-USERNAME/Tado-Heating-Control.git /opt/heating-brain
cd /opt/heating-brain

sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain
sudo mkdir -p /etc/heating-brain /var/lib/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /var/lib/heating-brain /var/log/heating-brain

python3 -m venv venv
./venv/bin/pip install -r app/requirements.txt

sudo cp app/config.example.yaml /etc/heating-brain/config.yaml
sudo $EDITOR /etc/heating-brain/config.yaml   # set your lat/lon and schedule

# First-run auth (interactive — you'll be given a URL to visit)
sudo -u heating-brain ./venv/bin/python -m app.orchestrator --config /etc/heating-brain/config.yaml

# Once that works, install as a service
sudo cp systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain
```

## Configuration

Everything lives in `/etc/heating-brain/config.yaml`. Key sections:

| Section | Purpose |
|---|---|
| `location` | Latitude/longitude for weather lookups |
| `tado.poll_interval_seconds` | How often to check/command Tado (default 60s) |
| `control.hysteresis_celsius` | Temperature band around threshold (default 0.5°C) |
| `control.min_state_change_interval_seconds` | Minimum time between on/off flips (default 600s) |
| `schedule` | List of time windows, each with days, start, end, threshold |
| `sensor.enabled` | Set `true` when you add an ESP32; indoor temp then overrides outdoor |

See `app/config.example.yaml` for a fully commented example.

## HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness check |
| `/status` | GET | Full state snapshot (for MM², dashboards) |
| `/sensor` | POST | Ingest indoor temp: `{"temperature_celsius": 20.3}` |

Optionally secure `/sensor` with a shared token via the `X-Sensor-Token` header — set `sensor.token` in config.

## MagicMirror² integration

Copy `mm-module/` into `~/MagicMirror/modules/MMM-HeatingBrain/` and add to your `config.js`:

```js
{
    module: "MMM-HeatingBrain",
    position: "top_left",
    config: {
        brainUrl: "http://localhost:8423",
        updateIntervalSeconds: 30,
    },
}
```

## Project layout

```
app/            # Python service (the "brain")
tests/          # Logic tests (no Tado/network required)
mm-module/      # MagicMirror² module
systemd/        # Service unit file
docs/           # Install + ESP32 guides
```

## Future: ESP32 indoor sensor

The `/sensor` endpoint is ready. See [docs/ESP32.md](docs/ESP32.md) for an example sketch that POSTs DS18B20 readings every 30 seconds. Once you enable `sensor.enabled: true` in the config, indoor readings take priority over outdoor when making decisions.

## License

MIT. See [LICENSE](LICENSE).

## Not affiliated with tado°

This project is not endorsed by or affiliated with tado° GmbH. Tado's REST API is unofficial and can change without notice — this project may break if they change the auth flow or endpoints again.
