"""
HTTP API for the heating brain.

Endpoints:
  GET  /status   — JSON snapshot for MagicMirror module, dashboards, etc.
  GET  /health   — cheap liveness check
  POST /sensor   — ingest indoor temperature from an ESP32 (future use)
                   Body: {"temperature_celsius": 20.3}
                   Optionally add a shared secret via the `X-Sensor-Token` header.

We use Flask because it's tiny, synchronous (matches the rest of the service),
and doesn't need async machinery for a ~10 req/hour API.
"""

from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, jsonify, request

from .state import SharedState

log = logging.getLogger(__name__)


def make_app(state: SharedState, sensor_token: Optional[str] = None) -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/status")
    def status():
        return jsonify(state.snapshot())

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

    return app
