# Installation guide

This is the full step-by-step. For a quick overview and feature list, see the top-level [README](../README.md).

Assumed platform: Raspberry Pi running a recent Raspberry Pi OS (Bookworm or later). Anything running Python ≥ 3.10 will work — it's tested on Pi 3B, Pi 4, and Pi 5.

**Pick your scenario:**
- Only one Pi → follow **§ A. Primary-only install**.
- Two or more Pis (one talks to Tado, others mirror the UI) → do **§ A** on the "main" Pi, then **§ B. Client install** on each additional Pi.

---

## A. Primary-only install

### 1. System dependencies

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 2. Clone the repo

```bash
sudo git clone https://github.com/Syntosys/Tado-Heating-Control.git /opt/heating-brain
cd /opt/heating-brain
```

### 3. Create the service user and directories

The service runs as an unprivileged system user — not `pi`, not `root` — so even if something goes wrong, the blast radius is small.

```bash
sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain || true
sudo mkdir -p /etc/heating-brain /var/lib/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /opt/heating-brain /var/lib/heating-brain /var/log/heating-brain
```

### 4. Python virtual environment

```bash
cd /opt/heating-brain
sudo -u heating-brain python3 -m venv venv
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt
```

### 5. Configure

```bash
sudo cp app/config.example.yaml /etc/heating-brain/config.yaml
sudo chown heating-brain:heating-brain /etc/heating-brain/config.yaml
sudo chmod 640 /etc/heating-brain/config.yaml
sudo nano /etc/heating-brain/config.yaml
```

Minimum edits:

- Leave `mode: primary` as-is
- **`location.latitude` / `longitude`** — look these up on openstreetmap.org (right-click → Show address)
- **`schedule`** — edit or add windows covering when you want heating to be eligible to run, with per-window `indoor_on_celsius`, `outdoor_on_celsius`, `indoor_off_celsius`, `outdoor_off_celsius` thresholds
- **`http.pin`** — a 4-digit string (e.g. `"1234"`) that will gate the web UI

If you're on the **free Tado tier** (no Auto-Assist), set `tado.poll_interval_seconds: 900` to stay under 100 calls/day. With Auto-Assist, leave the default 60s.

### 6. First-run Tado authentication

Tado requires a browser login the first time (OAuth2 device code flow, mandated since March 2025). Run the service interactively (not via systemd yet) so you can see the URL:

```bash
sudo -u heating-brain /opt/heating-brain/venv/bin/python \
    -m app.orchestrator --config /etc/heating-brain/config.yaml
```

You'll see:

```
============================================================
  TADO AUTHENTICATION REQUIRED
  Visit this URL in a browser and sign in:
  https://login.tado.com/oauth2/device?user_code=XXXXXX
  Waiting up to 5 minutes...
============================================================
```

Open that URL on any device, sign in to Tado, and confirm. The service will detect the approval and continue. You should then see:

```
Tado login successful — token saved.
... Tado home id: NNNNN
... Tado heating zone id (auto-detected): N
... Initial Tado state: OFF
... HTTP API listening on 0.0.0.0:8423
```

Press **Ctrl+C** to stop. The refresh token is saved at `/var/lib/heating-brain/tado_refresh_token` and will be used automatically on future runs (valid 30 days, rotated on each use).

### 7. Install as a systemd service

```bash
sudo cp /opt/heating-brain/systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain
```

Verify:

```bash
sudo systemctl status heating-brain --no-pager
sudo journalctl -u heating-brain -f   # Ctrl+C to stop tailing
curl -s http://localhost:8423/health
```

### 8. Open the web UI

From any device on the LAN: `http://<pi-ip>:8423`

Enter the PIN from step 5. You should see the **Now** tab with current weather, indoor temp, and ON/OFF state.

### 9. (Optional) Install the update-timer oneshot

If you want the web UI's **Settings → Update** button (and optional scheduled updates) to work, install the oneshot unit:

```bash
sudo cp /opt/heating-brain/systemd/heating-brain-update.service /etc/systemd/system/
sudo cp /opt/heating-brain/systemd/heating-brain-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
# Manual updates only:
sudo systemctl enable heating-brain-update.service
# OR scheduled daily auto-updates:
# sudo systemctl enable --now heating-brain-update.timer
```

You'll also need a sudoers rule so the web UI can trigger the oneshot without a password prompt. Create `/etc/sudoers.d/heating-brain` with:

```
heating-brain ALL=(root) NOPASSWD: /usr/bin/systemctl start heating-brain-update.service
```

### 10. (Optional) Wire up MagicMirror²

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
# or: pm2 restart MagicMirror
# or: sudo systemctl restart magicmirror
```

---

## B. Client install (additional devices)

Only do this if you already have a primary device running (§ A complete). A client runs the same web UI but proxies every API call to the primary — it makes zero external network calls.

### 1. System dependencies, clone, user, dirs, venv

Identical to §§ A.1–A.4:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
sudo git clone https://github.com/Syntosys/Tado-Heating-Control.git /opt/heating-brain
sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain || true
sudo mkdir -p /etc/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /opt/heating-brain /var/log/heating-brain
cd /opt/heating-brain
sudo -u heating-brain python3 -m venv venv
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt
```

### 2. Find the primary Pi's IP

On the **primary** Pi:

```bash
hostname -I
```

Note the LAN address (e.g. `10.0.0.42`).

### 3. Write the client config

On the **client** Pi:

```bash
sudo nano /etc/heating-brain/config.yaml
```

Paste this, **replacing `10.0.0.42` with the primary's IP**:

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

> **YAML gotcha:** every top-level key (`mode`, `primary_url`, `http`, `logging`) must start at column 1 — no leading spaces. Only nested keys (`host`, `port`, `level`, `file`) are indented. If the service fails to start with a YAML parse error, run `sudo cat -A /etc/heating-brain/config.yaml` to spot stray indentation or smart-quotes.

Set ownership:

```bash
sudo chown -R heating-brain:heating-brain /etc/heating-brain
sudo chmod 640 /etc/heating-brain/config.yaml
```

### 4. Install and start the service

```bash
sudo cp /opt/heating-brain/systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain
```

### 5. Verify

```bash
curl -s http://localhost:8423/health
# Expected: {"mode":"client","ok":true,"primary_ok":true}
```

If `primary_ok` is `false`, check:
- `sudo grep primary_url /etc/heating-brain/config.yaml` — right IP?
- `curl -v http://<primary-ip>:8423/health` from the client — does the primary answer?
- The primary's `http.host` must be `"0.0.0.0"`, not `"127.0.0.1"`.

Open `http://<client-ip>:8423` — same PIN, same state as the primary. Manual overrides, schedule edits, PIN changes made on the client all propagate to the primary.

### 6. (Optional) MagicMirror² on the client

Identical to § A.10 — the module just points at `localhost:8423`, and the proxy handles the rest. No Tado calls happen from the client Pi.

---

## Switching an existing install between primary and client

If you originally installed a second Pi in primary mode by mistake, switch it to client mode like this:

```bash
# 1. Stop the service (so the two Pis aren't both commanding Tado)
sudo systemctl stop heating-brain

# 2. Pull latest code (client_mode.py is only in recent commits)
cd /opt/heating-brain
sudo -u heating-brain git pull

# 3. Back up and rewrite the config (see § B.3 above for the client YAML)
sudo cp /etc/heating-brain/config.yaml /etc/heating-brain/config.yaml.bak
sudo nano /etc/heating-brain/config.yaml

# 4. Remove the orphan Tado token
sudo rm -f /var/lib/heating-brain/tado_refresh_token

# 5. Start back up
sudo systemctl start heating-brain
curl -s http://localhost:8423/health
```

---

## Verifying end-to-end behaviour

1. **Logs** — `sudo journalctl -u heating-brain -f`. You should see a `Decision:` line roughly every `tado.poll_interval_seconds` (default 60s).
2. **Status** — `curl -s http://localhost:8423/status | python3 -m json.tool`. `last_loop_at` should be recent.
3. **Web UI** — open `http://<pi-ip>:8423`, log in with the PIN, and check that outdoor temp, indoor temp (if applicable), and the current ON/OFF state are present.
4. **External-change detection** — use Alexa or the Tado app to toggle the heating. Within one tick (~60s), the **Now** tab's reason should read `external change detected (on)` or `…(off)` and show an active override.
5. **Force a decision** — temporarily edit one of your schedule window's thresholds (e.g. set `indoor_on_celsius: 30`) to trigger a state change, restart the service, and watch the logs. **Remember to change it back.**

## Troubleshooting

### "Device code expired"
Took longer than 5 minutes to complete the browser login. Re-run step A.6.

### "Refresh token rejected"
Token is older than 30 days, or a previous refresh crashed mid-rotation. The service will auto-fall-back to the device flow and print a new URL to the journal — `sudo journalctl -u heating-brain -n 50`. Or delete `/var/lib/heating-brain/tado_refresh_token` and re-run step A.6.

### "No HEATING zone found"
Unusual Tado topology (hot-water-only, multi-zone, etc). List your zones and pin `tado.zone_id` in the config to the right numeric ID.

### 429 / rate-limit errors
Over the 100/day free tier. Subscribe to Auto-Assist, or set `tado.poll_interval_seconds: 900`.

### Mirror shows "Brain unreachable" / fetch failed on a client
The most common cause is the primary binding to `127.0.0.1` only, or an old primary without the LAN-read allowance for `/status`. On the primary: `sudo -u heating-brain git pull && sudo systemctl restart heating-brain`. Make sure `http.host` is `"0.0.0.0"` in its config.

### YAML parse errors ("mapping values are not allowed here")
Indentation. Every top-level key must be flush-left. Run `sudo cat -A /etc/heating-brain/config.yaml` — trailing `$` is just the line ending, but any leading spaces shown on top-level keys are the bug.

### General log dump
```bash
sudo systemctl status heating-brain --no-pager
sudo journalctl -u heating-brain -n 100 --no-pager
```
