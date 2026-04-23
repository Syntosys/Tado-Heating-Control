# Tado-Heating-Control

A schedule- and temperature-driven controller for [tado°](https://www.tado.com) heating, designed to run on a Raspberry Pi — optionally alongside [MagicMirror²](https://magicmirror.builders).

> The code, package, and systemd service are internally named `heating-brain` for brevity — that's just the running process. This repo is the thing you clone.

Tado's own scheduling turns the heating on at a given time. This project adds a layer of logic on top: *"only heat in the mornings if it's actually cold enough outside, and only until the indoors reaches comfort temperature."* It combines a weekly schedule with four per-window thresholds (indoor-on, outdoor-on, indoor-off, outdoor-off) to give you a natural deadband without rapid cycling, and it keeps working if Alexa or anyone in the house overrides it manually.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tado API notes — read this first](#tado-api-notes--read-this-first)
- [Installation](#installation)
  - [Primary device (the one that talks to Tado)](#primary-device-the-one-that-talks-to-tado)
  - [Additional client devices (optional, multi-device mode)](#additional-client-devices-optional-multi-device-mode)
  - [MagicMirror² module (optional)](#magicmirror-module-optional)
- [Configuration reference](#configuration-reference)
- [HTTP API](#http-api)
- [How the decision engine works](#how-the-decision-engine-works)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [License](#license)

## Features

- **OAuth2 device code flow** — the auth method Tado made mandatory in March 2025. First run prints a URL; visit it once and you're set. Refresh tokens persist and auto-rotate.
- **Per-window four-threshold logic** — each schedule window has its own `indoor_on_celsius`, `outdoor_on_celsius`, `indoor_off_celsius`, `outdoor_off_celsius`. The spread between on/off thresholds gives you a deadband that prevents rapid cycling without needing a separate hysteresis setting.
- **Weekly schedule** — any number of windows, configurable by day (`mon`, `tue`, …, `weekdays`, `weekends`, `all`) and 24-hour time range. Outside every window, heating is forced OFF.
- **External-change detection** — if Alexa, the Tado app, or a family member flips the heating manually, the brain notices on the next tick, adopts the new state, and treats it as a manual override until the next schedule-window transition. Zero extra Tado API calls — it piggybacks on the zone-state fetch already used for indoor temperature.
- **Multi-device support** — run one Pi in `mode: primary` and any number of other devices in `mode: client`. Clients serve the same web UI but proxy every request to the primary, so the Tado API is only ever polled once regardless of how many dashboards you have.
- **Mobile web UI** — PIN-protected SPA served at port 8423. Four tabs:
  - **Now** — live status, weather, indoor temp, current decision, On/Off/Auto override buttons
  - **History** — 24 h and 7-day charts of indoor/outdoor temperature with heating-on bands
  - **Schedule** — add, edit, reorder, delete windows; written back to `config.yaml` atomically
  - **Settings** — installed version, one-click update-and-restart, PIN change
- **Optional ESP32 indoor sensor** — POST `{"temperature_celsius": 20.3}` to `/sensor`. When enabled, a fresh ESP32 reading takes priority over Tado's built-in sensor.
- **MagicMirror² module** — thin read-only tile polling `/status`. Survives the brain being restarted.
- **Systemd-hardened** — runs as an unprivileged system user with no shell, no home directory, protected filesystem, no new privileges.
- **Minimum-state-change interval** — configurable floor on how often we'll flip the heating, as a belt-and-braces guard against oscillation.

## Architecture

Single-device deployment (simplest — one Pi, one MagicMirror):

```
┌─────────────────────────────────────────────────────┐
│  Raspberry Pi (mode: primary)                       │
│                                                     │
│  ┌──────────────────────┐   ┌───────────────────┐   │
│  │  heating-brain       │   │  MagicMirror²     │   │
│  │  (systemd service)   │◄──┤  + MMM-HeatingBrain│  │
│  │                      │   │  (polls /status)  │   │
│  │  • Open-Meteo poll   │   └───────────────────┘   │
│  │  • Schedule engine   │                           │
│  │  • Tado device-flow  │   ┌───────────────────┐   │
│  │  • HTTP API :8423    │◄──┤  ESP32 (optional) │   │
│  └──────────┬───────────┘   │  POSTs /sensor    │   │
│             │               └───────────────────┘   │
└─────────────┼───────────────────────────────────────┘
              │
              ▼
         tado° cloud
```

Multi-device deployment — one primary does all the work, everything else is a thin client:

```
┌──────────────────────┐        ┌──────────────────────┐
│  Pi A (primary)      │        │  Pi B (client)       │
│                      │        │                      │
│  heating-brain       │        │  heating-brain       │
│  • Tado OAuth        │        │  • No Tado calls     │
│  • Weather poll      │  LAN   │  • No weather poll   │
│  • Decision engine   │◄───────┤  • Just proxies      │
│  • HTTP API :8423    │        │    /api, /status     │
│  • MagicMirror       │        │  • Same web UI       │
│                      │        │  • MagicMirror       │
└──────────┬───────────┘        └──────────────────────┘
           │
           ▼
      tado° cloud
```

The brain runs headless and keeps working even if MagicMirror crashes or the screen sleeps. The mirror module is a thin status viewer.

## Tado API notes — read this first

Tado made significant changes in 2025 that affect anyone integrating with their API:

- **Mandatory OAuth2 device code flow** as of 21 March 2025. Username/password is dead. First run opens a URL you visit in a browser to link the app to your account.
- **Rate limits:** 100 API calls/day on the free tier, 20,000/day with an [Auto-Assist](https://www.tado.com) subscription. This project's default 60-second poll interval uses ~1,440 calls/day — fine for Auto-Assist, **too many for the free tier**. If you're on the free tier, set `tado.poll_interval_seconds: 900` (15 min) in config to stay under 100/day.

## Installation

The full step-by-step is in [docs/INSTALL.md](docs/INSTALL.md). This section summarises the key commands.

### Primary device (the one that talks to Tado)

```bash
# 1. System deps
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip

# 2. Clone
sudo git clone https://github.com/Syntosys/Tado-Heating-Control.git /opt/heating-brain
cd /opt/heating-brain

# 3. Service user + dirs
sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain || true
sudo mkdir -p /etc/heating-brain /var/lib/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /opt/heating-brain /var/lib/heating-brain /var/log/heating-brain

# 4. Python venv
sudo -u heating-brain python3 -m venv venv
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt

# 5. Config
sudo cp app/config.example.yaml /etc/heating-brain/config.yaml
sudo chown heating-brain:heating-brain /etc/heating-brain/config.yaml
sudo chmod 640 /etc/heating-brain/config.yaml
sudo nano /etc/heating-brain/config.yaml    # set lat/lon, schedule, PIN, mode: primary

# 6. First-run Tado authentication (interactive — prints URL)
sudo -u heating-brain ./venv/bin/python -m app.orchestrator --config /etc/heating-brain/config.yaml
# Visit the URL, sign in, confirm. Ctrl+C once "HTTP API listening" appears.

# 7. Install as a service
sudo cp systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain

# 8. Verify
sudo systemctl status heating-brain
curl -s http://localhost:8423/health
```

Open `http://<pi-ip>:8423` from any device on your LAN. Enter the PIN you set.

### Additional client devices (optional, multi-device mode)

If you have a second Pi (e.g. another MagicMirror, a kitchen display) and want it to show the same state without doubling your Tado API usage:

```bash
# 1. System deps + service user + dirs (same as primary steps 1–3)
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip
sudo git clone https://github.com/Syntosys/Tado-Heating-Control.git /opt/heating-brain
sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain || true
sudo mkdir -p /etc/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /opt/heating-brain /var/log/heating-brain

# 2. Python venv (same)
cd /opt/heating-brain
sudo -u heating-brain python3 -m venv venv
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt

# 3. Minimal client config — replace 10.0.0.42 with your primary Pi's IP
sudo nano /etc/heating-brain/config.yaml
```

Paste this and save:

```yaml
mode: client
primary_url: "http://10.0.0.42:8423"

http:
  host: "0.0.0.0"
  port: 8423

logging:
  level: INFO
  file: /var/log/heating-brain/heating-brain.log
```

```bash
# 4. Install + start service
sudo cp systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain

# 5. Verify — should return {"ok":true,"mode":"client","primary_ok":true}
curl -s http://localhost:8423/health
```

Clients need no Tado token, no weather polling, and no schedule — they pull everything from the primary. Open `http://<client-ip>:8423` and you'll see the same live state.

### MagicMirror² module (optional)

Works identically on primary and client devices — the module polls `localhost:8423/status`, which on clients is transparently proxied to the primary.

```bash
cp -r /opt/heating-brain/mm-module ~/MagicMirror/modules/MMM-HeatingBrain
```

Add to `~/MagicMirror/config/config.js`:

```js
{
    module: "MMM-HeatingBrain",
    position: "top_left",
    config: {
        brainUrl: "http://localhost:8423",
        updateIntervalSeconds: 30,
    },
},
```

Restart MagicMirror:

```bash
pm2 restart mm
```

## Configuration reference

Full commented example: [`app/config.example.yaml`](app/config.example.yaml). Key sections:

| Section | Key | Purpose |
|---|---|---|
| top-level | `mode` | `primary` (default) or `client`. Only one device per home should be `primary`. |
| top-level | `primary_url` | Required when `mode: client`. Points at the primary's HTTP API. |
| `location` | `latitude`, `longitude` | Used for Open-Meteo weather lookups. Find yours at openstreetmap.org. |
| `weather` | `poll_interval_seconds` | How often to re-fetch outdoor temp. 600s (10 min) is plenty. |
| `tado` | `poll_interval_seconds` | Tick rate. 60s default uses ~1,440 calls/day. Set 900 on the free tier. |
| `tado` | `token_file` | Where to persist the OAuth refresh token. Default is fine. |
| `tado` | `zone_id` | Leave `null` to auto-detect your HEATING zone on first run. |
| `control` | `heat_on_target_celsius` | Target temperature set on Tado when heating is turned on. |
| `control` | `min_state_change_interval_seconds` | Floor on how often we flip on↔off. Default 600s. |
| `control` | `off_behavior` | `off` = hard manual-off overlay, `auto` = clear overlay (return to Tado's own schedule). |
| `control` | `on_overlay_termination` | `MANUAL` (stays until changed), `NEXT_TIME_BLOCK`, or `TIMER` (uses `timer_minutes`). |
| `control` | `detect_external_changes` | Default `true`. Detects Alexa/app-initiated changes and adopts them as overrides. |
| `control` | `external_change_cooldown_seconds` | Default 120. Ignore mismatches this soon after our own command (avoids false positives from propagation delay). |
| `schedule` | list of windows | Each window needs `name`, `days`, `start`, `end`, and the four thresholds. |
| `http` | `host`, `port` | Default `0.0.0.0:8423`. Leave as-is unless you've got port conflicts. |
| `http` | `pin` | 4-digit PIN for the web UI. Leave empty to disable auth (not recommended outside a trusted LAN). |
| `http` | `override_expiry_minutes` | How long a manual or external override lasts before auto-resuming schedule. Default 120. |
| `sensor` | `enabled`, `max_age_seconds`, `token` | Enable when an ESP32 is posting readings to `/sensor`. |
| `logging` | `level`, `file` | `INFO` / `DEBUG`. File path goes to both file and journal. |

### Schedule windows

Each window takes four thresholds. **All must be true** to change state:

- Turn ON when `indoor < indoor_on_celsius` AND `outdoor < outdoor_on_celsius`
- Turn OFF when `indoor > indoor_off_celsius` AND `outdoor > outdoor_off_celsius`
- Otherwise: hold current state (natural deadband)

Tip: set `indoor_off > indoor_on` and `outdoor_off > outdoor_on` (e.g. on@18/15, off@20/17) to create a comfortable hysteresis band without needing a separate setting.

## HTTP API

All `/api/*` endpoints require a valid PIN cookie unless noted. `/status` and `/api/status` are readable from any LAN (RFC1918) IP without a PIN — so MagicMirror² modules and other dashboards can poll without authentication.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | none | Liveness check. Client mode also reports `primary_ok`. |
| `/status` | GET | LAN-open | Full state snapshot (for MM², dashboards) |
| `/sensor` | POST | optional `X-Sensor-Token` | Ingest indoor temp: `{"temperature_celsius": 20.3}` |
| `/api/auth` | POST | none | Body `{"pin": "1234"}` → sets signed session cookie |
| `/api/logout` | POST | none | Clears session cookie |
| `/api/status` | GET | LAN-open | Same snapshot as `/status` |
| `/api/schedule` | GET | PIN | List current schedule windows |
| `/api/schedule` | PUT | PIN | Replace schedule atomically (persisted to `config.yaml`) |
| `/api/override` | POST | PIN or LAN | `{"mode":"on"|"off"|"auto"}` |
| `/api/history` | GET | PIN | `?hours=24` — recent temperature/heating samples |
| `/api/pin` | POST | PIN | Change the stored PIN |
| `/api/version` | GET | PIN | Current git commit (short + full SHA) |
| `/api/update` | POST | PIN | `git fetch`+`pull`+restart via systemd oneshot |

## How the decision engine works

Every tick (default every 60s):

1. **Fetch outdoor weather** if the weather interval has elapsed (default 10 min — not every tick).
2. **Fetch Tado zone state** — one API call that provides indoor temperature, setpoint, and the actual current power state.
3. **Reconcile external changes.** If Tado's actual power state doesn't match our last-commanded state, and more than `external_change_cooldown_seconds` have passed since we ourselves commanded it, this is someone else (Alexa, Tado app, family). We adopt the new state as a manual override.
4. **Clear override if a new window started.** Manual overrides auto-clear at the next schedule-window transition.
5. **Pick the active window** based on day and time.
6. **Decide.** If an override is active, honour it. Otherwise: outside every window → OFF. Inside a window → evaluate the four thresholds and either flip to ON, flip to OFF, or hold.
7. **Apply.** If the desired state differs from the commanded state AND `min_state_change_interval_seconds` has elapsed (overrides bypass this), send the command to Tado.
8. **Record.** Write a history sample and update the shared snapshot for the HTTP API.

## Updating

The web UI has a **Settings → Update** button that runs `git pull` and restarts the service. You can also do it manually:

```bash
cd /opt/heating-brain
sudo -u heating-brain git pull
sudo systemctl restart heating-brain
sudo journalctl -u heating-brain -f
```

On a client-mode Pi the same commands apply — the proxy is part of this repo too.

## Troubleshooting

### `"Device code expired"` on first run
You took longer than 5 minutes to complete the browser login. Re-run the first-run auth step.

### `"Refresh token rejected — falling back to device flow"` in journal
The token is older than 30 days, or a previous refresh crashed mid-rotation. The service auto-prints a new device-code URL in the journal — `sudo journalctl -u heating-brain -n 50` to find it. If nothing's there, delete `/var/lib/heating-brain/tado_refresh_token` and re-run the first-run auth.

### `"No HEATING zone found"`
Your Tado setup might be hot-water-only or unusual. List your zones and set `tado.zone_id` explicitly:

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
     https://my.tado.com/api/v2/homes/YOUR_HOME_ID/zones | python3 -m json.tool
```

### `429` errors from Tado
You're over the 100/day free-tier limit. Subscribe to Auto-Assist, or set `tado.poll_interval_seconds: 900`.

### Mirror shows `"Brain unreachable"` / 401
- Is the service running? `sudo systemctl status heating-brain`
- Is port 8423 reachable? `curl -s http://localhost:8423/health`
- If you're on a client device, check `primary_ok` in the health output. If false, the client can't reach the primary — verify `primary_url` in `/etc/heating-brain/config.yaml` and that the primary is actually listening on `0.0.0.0` (not `127.0.0.1`).
- If `primary_ok` is true but the mirror still shows unreachable, the primary may need updating to a commit that allows LAN reads of `/status`. `cd /opt/heating-brain && sudo -u heating-brain git pull && sudo systemctl restart heating-brain` on the primary.

### Client mode: `"primary unreachable"` in the web UI
Same check as above — the client's proxy couldn't reach the primary. Most common cause is the primary binding to `127.0.0.1` only. Set `http.host: "0.0.0.0"` in the primary's config and restart.

### YAML parse errors
`sudo cat -A /etc/heating-brain/config.yaml` to check for stray indentation or smart-quotes. Every top-level key must be flush to the left margin, with nested keys indented by 2 spaces.

### Logs in general
```bash
sudo systemctl status heating-brain --no-pager
sudo journalctl -u heating-brain -n 100 --no-pager
```

## Project layout

```
app/                  Python service (the "brain")
├── orchestrator.py   Main entrypoint + control loop
├── client_mode.py    Client-mode runner (pure HTTP proxy)
├── tado_client.py    OAuth device flow + Tado API calls
├── weather.py        Open-Meteo client
├── schedule.py       Schedule window parser/matcher
├── decision.py       Pure decision logic (no side effects)
├── state.py          Thread-safe shared state
├── history.py        In-memory rolling history buffer
├── http_api.py       Flask routes (status, /api/*, web UI)
├── auth.py           PIN + signed-cookie session
├── config_writer.py  Atomic YAML writeback for schedule / PIN changes
└── web/              Static assets for the mobile web UI
tests/                Logic tests (no Tado/network required)
mm-module/            MagicMirror² module (read-only tile)
systemd/              Service unit + update oneshot
docs/                 Install guide, ESP32 guide, GitHub guide
```

## ESP32 indoor sensor

The `/sensor` endpoint is ready. See [docs/ESP32.md](docs/ESP32.md) for an example sketch that POSTs DS18B20 readings every 30 seconds. Enable `sensor.enabled: true` to make indoor readings take priority over Tado's own sensor.

## License

MIT. See [LICENSE](LICENSE).

## Not affiliated with tado°

This project is not endorsed by or affiliated with tado° GmbH. Tado's REST API is unofficial and has changed before — this project may break if they change the auth flow or endpoints again.
