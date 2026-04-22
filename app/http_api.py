"""
HTTP API for the heating brain.

Legacy endpoints (backwards-compatible):
  GET  /status   — JSON snapshot for MagicMirror module
  GET  /health   — cheap liveness check (no auth)
  POST /sensor   — ingest indoor temperature from ESP32 (X-Sensor-Token auth)

New endpoints (PIN cookie required except /health and /sensor):
  GET  /         — mobile web UI (HTML)
  POST /api/auth         — {"pin": "1234"} -> sets session cookie
  POST /api/logout       — clears session cookie
  GET  /api/status       — current snapshot JSON
  GET  /api/schedule     — list of schedule windows
  PUT  /api/schedule     — replace full schedule list (atomic YAML write)
  POST /api/override     — {"mode": "on"|"off"|"auto"}
  GET  /api/history      — ?hours=24, returns history points
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    request,
    send_from_directory,
)

from .auth import COOKIE_NAME, COOKIE_MAX_AGE_DAYS, check_pin, make_cookie_value, verify_cookie_value
from .config_writer import write_schedule
from .history import HistoryBuffer
from .schedule import Window, parse_schedule
from .state import SharedState

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent / "web"


def _require_pin_cookie(pin: Optional[str]):
    """
    Decorator factory. If pin is None (not configured), all requests pass through.
    Otherwise checks for a valid signed session cookie.
    """
    def decorator(f):
        from functools import wraps

        @wraps(f)
        def wrapped(*args, **kwargs):
            if pin is None:
                return f(*args, **kwargs)
            cookie_val = request.cookies.get(COOKIE_NAME, "")
            if not verify_cookie_value(cookie_val):
                return jsonify({"error": "unauthorized"}), 401
            return f(*args, **kwargs)

        return wrapped

    return decorator


def make_app(
    state: SharedState,
    history: HistoryBuffer,
    schedule_windows: list[Window],
    config_path: Path,
    sensor_token: Optional[str] = None,
    pin: Optional[str] = None,
    override_expiry_minutes: int = 120,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    # Mutable reference so PUT /api/schedule can update the in-memory list.
    _schedule: list[Window] = schedule_windows

    auth_required = _require_pin_cookie(pin)

    # ------------------------------------------------------------------
    # No-auth endpoints
    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/sensor")
    def ingest_sensor():
        if sensor_token:
            provided = request.headers.get("X-Sensor-Token", "")
            if provided != sensor_token:
                return jsonify({"error": "unauthorized"}), 401
        try:
            body = request.get_json(force=True, silent=False)
            temp = float(body["temperature_celsius"])
        except (TypeError, KeyError, ValueError) as e:
            return jsonify({"error": f"bad request: {e}"}), 400
        if not (-40 <= temp <= 80):
            return jsonify({"error": "temperature out of plausible range"}), 400
        state.record_indoor(temp)
        log.debug("Sensor reading: %.2f°C", temp)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    @app.post("/api/auth")
    def api_auth():
        body = request.get_json(force=True, silent=True) or {}
        provided_pin = str(body.get("pin", ""))
        if pin is None:
            # No PIN configured — always succeed.
            cookie_val = make_cookie_value()
            resp = make_response(jsonify({"ok": True}))
            resp.set_cookie(
                COOKIE_NAME,
                cookie_val,
                max_age=COOKIE_MAX_AGE_DAYS * 86400,
                httponly=True,
                samesite="Lax",
            )
            return resp
        if not check_pin(provided_pin, pin):
            return jsonify({"error": "incorrect PIN"}), 401
        cookie_val = make_cookie_value()
        resp = make_response(jsonify({"ok": True}))
        resp.set_cookie(
            COOKIE_NAME,
            cookie_val,
            max_age=COOKIE_MAX_AGE_DAYS * 86400,
            httponly=True,
            samesite="Lax",
        )
        return resp

    @app.post("/api/logout")
    def api_logout():
        resp = make_response(jsonify({"ok": True}))
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ------------------------------------------------------------------
    # Web UI — serves index.html; JS fetches the API endpoints
    # ------------------------------------------------------------------

    @app.get("/")
    def web_ui():
        # Check auth — redirect to PIN page handled client-side.
        if pin is not None:
            cookie_val = request.cookies.get(COOKIE_NAME, "")
            if not verify_cookie_value(cookie_val):
                # Still serve the page — JS will show the PIN screen.
                pass
        return send_from_directory(str(_WEB_DIR), "index.html")

    @app.get("/app.css")
    def web_css():
        return send_from_directory(str(_WEB_DIR), "app.css")

    @app.get("/app.js")
    def web_js():
        return send_from_directory(str(_WEB_DIR), "app.js")

    # ------------------------------------------------------------------
    # JSON API — all require PIN cookie
    # ------------------------------------------------------------------

    @app.get("/status")
    @auth_required
    def status():
        return jsonify(state.snapshot())

    @app.get("/api/status")
    @auth_required
    def api_status():
        return jsonify(state.snapshot())

    @app.get("/api/schedule")
    @auth_required
    def api_get_schedule():
        return jsonify([w.to_dict() for w in _schedule])

    @app.put("/api/schedule")
    @auth_required
    def api_put_schedule():
        nonlocal _schedule
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, list):
            return jsonify({"error": "body must be a JSON array of window objects"}), 400
        try:
            new_windows = parse_schedule(body)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        try:
            write_schedule(config_path, new_windows)
        except OSError as e:
            log.error("Failed to write schedule to config: %s", e)
            return jsonify({"error": f"failed to persist: {e}"}), 500
        _schedule.clear()
        _schedule.extend(new_windows)
        return jsonify({"ok": True, "count": len(_schedule)})

    @app.post("/api/override")
    @auth_required
    def api_override():
        body = request.get_json(force=True, silent=True) or {}
        mode = body.get("mode", "")
        if mode == "auto":
            state.clear_override()
            return jsonify({"ok": True, "mode": "auto"})
        if mode in ("on", "off"):
            state.set_override(mode, override_expiry_minutes)
            return jsonify({"ok": True, "mode": mode})
        return jsonify({"error": "mode must be 'on', 'off', or 'auto'"}), 400

    @app.get("/api/version")
    @auth_required
    def api_version():
        repo_dir = Path("/opt/heating-brain")
        try:
            short = subprocess.check_output(
                ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
            full = subprocess.check_output(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
            return jsonify({"commit": full, "short": short})
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return jsonify({"error": f"git unavailable: {e}", "commit": None, "short": None}), 200

    @app.post("/api/update")
    @auth_required
    def api_update():
        # Fire-and-forget: trigger the systemd oneshot update service.
        # It pulls, chowns, and restarts heating-brain if there are changes.
        # Requires sudoers entry permitting:
        #   heating-brain ALL=(root) NOPASSWD: /bin/systemctl start heating-brain-update.service
        try:
            proc = subprocess.run(
                ["sudo", "-n", "/bin/systemctl", "start", "--no-block", "heating-brain-update.service"],
                capture_output=True, timeout=10,
            )
            if proc.returncode != 0:
                return jsonify({
                    "error": "failed to trigger update",
                    "stderr": proc.stderr.decode(errors="replace").strip(),
                }), 500
            return jsonify({"triggered": True}), 202
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return jsonify({"error": f"update failed: {e}"}), 500

    @app.get("/api/history")
    @auth_required
    def api_history():
        try:
            hours = float(request.args.get("hours", 24))
            hours = max(0.1, min(hours, 168))  # clamp: 6 min – 7 days
        except ValueError:
            hours = 24.0
        return jsonify(history.get(hours))

    return app
