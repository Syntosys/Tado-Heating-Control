# Installation guide

This guide assumes a Raspberry Pi running a recent Raspberry Pi OS (Bookworm or later), with MagicMirror² already installed.

## 1. Clone the repo

```bash
sudo git clone https://github.com/YOUR-USERNAME/Tado-Heating-Control.git /opt/heating-brain
cd /opt/heating-brain
```

## 2. Create the service user and directories

We run the service as an unprivileged user (not `pi`, not `root`) so that even if something goes wrong, the blast radius is small.

```bash
sudo useradd --system --home-dir /opt/heating-brain --shell /usr/sbin/nologin heating-brain

sudo mkdir -p /etc/heating-brain /var/lib/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /var/lib/heating-brain /var/log/heating-brain
sudo chown -R heating-brain:heating-brain /opt/heating-brain
```

## 3. Install Python dependencies

```bash
cd /opt/heating-brain
sudo -u heating-brain python3 -m venv venv
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt
```

## 4. Configure

```bash
sudo cp app/config.example.yaml /etc/heating-brain/config.yaml
sudo chown heating-brain:heating-brain /etc/heating-brain/config.yaml
sudo chmod 640 /etc/heating-brain/config.yaml
sudo nano /etc/heating-brain/config.yaml
```

At minimum, set:

- **`location.latitude` / `longitude`** — look these up on Google Maps (right-click → coordinates)
- **`schedule`** — edit to match when you want the heating to be eligible to run
- **Each window's `outdoor_threshold_celsius`** — the temperature below which heating should come on during that window

If you're on the **free Tado tier** (no Auto-Assist), set `tado.poll_interval_seconds: 900` to stay under 100 calls/day. With Auto-Assist, leave the default 60s.

## 5. First-run authentication

Tado requires a browser login the first time. Run the service interactively (not via systemd yet) so you can see the URL:

```bash
sudo -u heating-brain /opt/heating-brain/venv/bin/python \
    -m app.orchestrator --config /etc/heating-brain/config.yaml
```

You'll see something like:

```
============================================================
  TADO AUTHENTICATION REQUIRED
  Visit this URL in a browser and sign in:
  https://login.tado.com/oauth2/device?user_code=XXXXXX
  Waiting up to 5 minutes...
============================================================
```

Open that URL on any device (your phone is fine), sign in to Tado, and confirm. The service will detect the approval and continue. You should then see:

```
Tado login successful — token saved.
... Tado home id: NNNNN
... Tado heating zone id (auto-detected): N
... Initial Tado state: off
... HTTP API listening on 0.0.0.0:8423
```

Press Ctrl+C to stop. The refresh token is now saved at `/var/lib/heating-brain/tado_refresh_token` and will be used automatically on future runs (valid 30 days, rotated on each use).

## 6. Install as a systemd service

```bash
sudo cp /opt/heating-brain/systemd/heating-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heating-brain
```

Check it's running:

```bash
sudo systemctl status heating-brain
sudo journalctl -u heating-brain -f
```

Test the HTTP API:

```bash
curl http://localhost:8423/health
curl http://localhost:8423/status | python3 -m json.tool
```

## 7. Wire up MagicMirror²

```bash
cp -r /opt/heating-brain/mm-module ~/MagicMirror/modules/MMM-HeatingBrain
```

Edit `~/MagicMirror/config/config.js` and add to the `modules` array:

```js
{
    module: "MMM-HeatingBrain",
    position: "top_left",          // or wherever suits your layout
    config: {
        brainUrl: "http://localhost:8423",
        updateIntervalSeconds: 30,
    },
},
```

Restart MagicMirror²:

```bash
pm2 restart MagicMirror   # if using pm2
# or whatever your MM² startup method is
```

## 8. Verifying it works

1. **Check the logs** — `sudo journalctl -u heating-brain -f`. You should see a "Decision:" line every minute.
2. **Check `/status`** — `curl http://localhost:8423/status | python3 -m json.tool`. `last_loop_at` should be recent.
3. **Check the mirror** — the HeatingBrain panel should show outdoor temp, indoor "no sensor", and the current ON/OFF pill.
4. **Force a state change** — temporarily edit `outdoor_threshold_celsius` in config to something that will trigger (e.g. `30`), restart the service, and watch the logs for a Tado command. **Remember to change it back.**

## Troubleshooting

### "Device code expired"

You took longer than 5 minutes to complete the browser login. Just run step 5 again.

### "Refresh token rejected"

The refresh token is older than 30 days, or was rotated but not saved (e.g. the service crashed at the wrong moment). The service will automatically fall back to the device flow and print a new URL. If you're running via systemd it'll log this to the journal — `sudo journalctl -u heating-brain -n 50` and grab the URL from there. **Or** just stop the service, delete `/var/lib/heating-brain/tado_refresh_token`, and re-run step 5 interactively.

### "No HEATING zone found"

Your Tado setup might have hot-water-only zones or an unusual topology. List your zones to see what's there:

```bash
curl -H "Authorization: Bearer $(cat /tmp/tado_access_token_for_debugging)" \
     https://my.tado.com/api/v2/homes/YOUR_HOME_ID/zones | python3 -m json.tool
```

Then set `tado.zone_id` in the config to the correct numeric ID.

### Rate limit errors (429)

You're over Tado's 100/day free tier. Either subscribe to Auto-Assist, or increase `tado.poll_interval_seconds` to `900` or higher.

### The mirror shows "Brain unreachable"

Check that the service is actually running (`systemctl status heating-brain`) and that port 8423 is reachable (`curl http://localhost:8423/health`). If the mirror runs on a different machine than the brain, update `brainUrl` to the Pi's LAN IP, e.g. `http://192.168.1.50:8423`.
