"""
Client mode runner.

In client mode, this device runs no control loop and talks to no external
APIs. It serves the web UI locally and proxies every HTTP API call to the
primary Pi's heating-brain service. Only one device on your network should
run in primary mode — all other devices should run in client mode so they
show the same state and share a single Tado API budget.

Config requirements:
  mode: client
  primary_url: "http://<primary-ip>:8423"
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent / "web"

# Forwarded request/response headers that would otherwise confuse Flask/requests
# (connection framing, transfer encoding, etc).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "content-encoding", "host",
}


def _configure_logging(cfg: dict) -> None:
    lcfg = cfg.get("logging", {}) or {}
    level = getattr(logging, lcfg.get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def make_client_app(primary_url: str) -> Flask:
    app = Flask(__name__, static_folder=None)
    primary_url = primary_url.rstrip("/")

    # ----- Static web UI (served locally; only the API is proxied) -----
    @app.get("/")
    def web_ui():
        return send_from_directory(str(_WEB_DIR), "index.html")

    @app.get("/app.css")
    def web_css():
        return send_from_directory(str(_WEB_DIR), "app.css")

    @app.get("/app.js")
    def web_js():
        return send_from_directory(str(_WEB_DIR), "app.js")

    @app.get("/health")
    def health():
        # Report ourselves healthy AND whether the primary is reachable so
        # the user can diagnose a broken link from the client side.
        try:
            r = requests.get(f"{primary_url}/health", timeout=3)
            primary_ok = r.ok
        except requests.RequestException:
            primary_ok = False
        return jsonify({"ok": True, "mode": "client", "primary_ok": primary_ok})

    def _proxy(path: str) -> Response:
        target = f"{primary_url}/{path.lstrip('/')}"
        # Forward headers except hop-by-hop and Host.
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        try:
            upstream = requests.request(
                method=request.method,
                url=target,
                headers=fwd_headers,
                params=request.args,
                data=request.get_data(),
                cookies=request.cookies,
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            log.warning("Proxy to %s failed: %s", target, e)
            return Response(
                response=f'{{"error": "primary unreachable: {e}"}}',
                status=502,
                mimetype="application/json",
            )

        resp_headers = [
            (k, v) for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP
        ]
        return Response(
            upstream.content,
            status=upstream.status_code,
            headers=resp_headers,
            content_type=upstream.headers.get("Content-Type"),
        )

    # ----- Local endpoints that must NOT be proxied -----
    # /api/version and /api/update report and act on THIS Pi's git clone,
    # not the primary's. Each Pi has its own /opt/heating-brain, so updates
    # need to be initiated locally. Registered before the catch-all below so
    # Flask's router picks the more specific route first.

    @app.get("/api/version")
    def local_version() -> Response:
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
            return jsonify({"error": f"git unavailable: {e}", "commit": None, "short": None})

    @app.post("/api/update")
    def local_update() -> Response:
        repo_dir = "/opt/heating-brain"
        git_base = ["git", "-C", repo_dir, "-c", f"safe.directory={repo_dir}"]
        try:
            fetch = subprocess.run(
                git_base + ["fetch", "origin", "main"],
                capture_output=True, timeout=30,
            )
            if fetch.returncode != 0:
                stderr = (fetch.stderr or b"").decode(errors="replace").strip()
                log.warning("git fetch failed (rc=%d): %s", fetch.returncode, stderr)
                return jsonify({
                    "error": f"git fetch failed: {stderr or 'exit code ' + str(fetch.returncode)}",
                    "hint": "check that /opt/heating-brain is owned by heating-brain:heating-brain",
                }), 500
            local = subprocess.check_output(
                git_base + ["rev-parse", "HEAD"], timeout=5,
            ).decode().strip()
            remote = subprocess.check_output(
                git_base + ["rev-parse", "origin/main"], timeout=5,
            ).decode().strip()
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return jsonify({"error": f"git failed: {e}"}), 500

        if local == remote:
            return jsonify({"updated": False, "version": local[:7]})

        try:
            subprocess.Popen(
                ["sudo", "-n", "/usr/bin/systemctl", "start", "heating-brain-update.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, FileNotFoundError) as e:
            return jsonify({"error": f"trigger failed: {e}"}), 500
        return jsonify({"updated": True, "before": local[:7], "after": remote[:7]}), 202

    # ----- Proxy every other API path straight through to the primary -----
    @app.route("/api/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
    def proxy_api(subpath: str) -> Response:
        return _proxy(f"/api/{subpath}")

    @app.route("/status", methods=["GET"])
    def proxy_status() -> Response:
        return _proxy("/status")

    @app.route("/sensor", methods=["POST"])
    def proxy_sensor() -> Response:
        return _proxy("/sensor")

    return app


def run_client(cfg: dict[str, Any]) -> None:
    _configure_logging(cfg)
    primary_url = (cfg.get("primary_url") or "").strip()
    if not primary_url:
        raise SystemExit(
            "mode: client requires 'primary_url' in config "
            "(e.g. 'http://192.168.1.42:8423')."
        )

    http_cfg = cfg.get("http", {}) or {}
    host = http_cfg.get("host", "0.0.0.0")
    port = int(http_cfg.get("port", 8423))

    app = make_client_app(primary_url)
    log.info("Client mode — proxying to %s, listening on %s:%d", primary_url, host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
