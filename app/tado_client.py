"""
Tado client using the OAuth2 device code flow mandated since 21 March 2025.

Reference: https://support.tado.com/en/articles/8565472-how-do-i-authenticate-to-access-the-rest-api

This module intentionally does NOT use a third-party Tado library. The device flow
is simple enough that a ~200-line implementation avoids a dependency we'd have to
track as Tado keeps changing their API.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

# The client_id Tado provides for third-party/unofficial clients.
# This is public and documented in their help article.
TADO_CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"

TADO_OAUTH_DEVICE_AUTHORIZE = "https://login.tado.com/oauth2/device_authorize"
TADO_OAUTH_TOKEN = "https://login.tado.com/oauth2/token"
TADO_API_BASE = "https://my.tado.com/api/v2"


class TadoAuthError(Exception):
    pass


class TadoApiError(Exception):
    pass


class TadoClient:
    """
    Thin Tado API client with persistent refresh-token storage.

    Usage:
        client = TadoClient(token_file="/var/lib/heating-brain/tado_refresh_token")
        client.ensure_authenticated()  # may block on first run — prints URL to stdout
        home_id = client.get_home_id()
        zones = client.get_zones(home_id)
        client.set_heating_on(home_id, zone_id, target_celsius=20.0)
    """

    # We proactively refresh if the access token has less than this many seconds left.
    ACCESS_TOKEN_REFRESH_MARGIN = 120

    def __init__(self, token_file: str | Path):
        self.token_file = Path(token_file)
        self._access_token: Optional[str] = None
        self._access_token_expires_at: float = 0.0
        self._refresh_token: Optional[str] = self._load_refresh_token()

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------
    def _load_refresh_token(self) -> Optional[str]:
        if not self.token_file.exists():
            return None
        try:
            return self.token_file.read_text().strip() or None
        except OSError as e:
            log.warning("Couldn't read token file %s: %s", self.token_file, e)
            return None

    def _save_refresh_token(self, token: str) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically + chmod 600 so other users on the Pi can't read it.
        tmp = self.token_file.with_suffix(".tmp")
        tmp.write_text(token)
        os.chmod(tmp, 0o600)
        tmp.replace(self.token_file)
        log.debug("Saved refresh token to %s", self.token_file)

    # ------------------------------------------------------------------
    # Device code flow (first login only)
    # ------------------------------------------------------------------
    def _device_code_login(self) -> None:
        """
        Initiate device code flow. Prints a URL to stdout and blocks until the user
        completes authentication in a browser, or the 5-minute code expires.
        """
        log.info("No refresh token found — starting device code login.")
        resp = requests.post(
            TADO_OAUTH_DEVICE_AUTHORIZE,
            params={"client_id": TADO_CLIENT_ID, "scope": "offline_access"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        device_code = data["device_code"]
        verification_uri = data["verification_uri_complete"]
        interval = int(data.get("interval", 5))
        expires_in = int(data.get("expires_in", 300))

        # Print prominently — the user needs to see this even in systemd logs.
        msg = (
            "\n"
            "============================================================\n"
            "  TADO AUTHENTICATION REQUIRED\n"
            "  Visit this URL in a browser and sign in:\n"
            f"  {verification_uri}\n"
            "  Waiting up to 5 minutes...\n"
            "============================================================\n"
        )
        print(msg, flush=True)
        log.info("Device code auth URL: %s", verification_uri)

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            poll = requests.post(
                TADO_OAUTH_TOKEN,
                params={
                    "client_id": TADO_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=10,
            )
            if poll.status_code == 200:
                tok = poll.json()
                self._apply_token_response(tok)
                print("Tado login successful — token saved.", flush=True)
                return
            # 400 with error=authorization_pending/slow_down is the normal waiting case.
            err = poll.json().get("error") if poll.headers.get("content-type", "").startswith("application/json") else None
            if err in ("authorization_pending", "slow_down"):
                continue
            raise TadoAuthError(f"Device code login failed: {poll.status_code} {poll.text}")

        raise TadoAuthError("Device code expired — user did not complete login in time.")

    # ------------------------------------------------------------------
    # Refresh flow
    # ------------------------------------------------------------------
    def _refresh_access_token(self) -> None:
        if not self._refresh_token:
            raise TadoAuthError("No refresh token available — cannot refresh.")
        resp = requests.post(
            TADO_OAUTH_TOKEN,
            params={
                "client_id": TADO_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            # Refresh token rotation means an old one is revoked once used. If the
            # last refresh crashed between receiving a new token and saving it, or
            # if 30 days have passed, we need to re-authenticate from scratch.
            log.warning(
                "Refresh token rejected (%s %s) — falling back to device flow.",
                resp.status_code, resp.text
            )
            self._refresh_token = None
            self._device_code_login()
            return
        self._apply_token_response(resp.json())

    def _apply_token_response(self, tok: dict[str, Any]) -> None:
        self._access_token = tok["access_token"]
        self._access_token_expires_at = time.time() + int(tok.get("expires_in", 3600))
        new_refresh = tok.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            self._save_refresh_token(new_refresh)

    def ensure_authenticated(self) -> None:
        """Ensure we have a valid access token, refreshing or doing device login as needed."""
        if self._access_token and time.time() < (self._access_token_expires_at - self.ACCESS_TOKEN_REFRESH_MARGIN):
            return
        if self._refresh_token:
            try:
                self._refresh_access_token()
                return
            except Exception as e:
                log.warning("Refresh failed: %s — falling back to device flow.", e)
        self._device_code_login()

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.ensure_authenticated()
        url = f"{TADO_API_BASE}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        r = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if r.status_code == 401:
            # Access token went stale between our check and the call. Force refresh once.
            log.info("Got 401 from Tado — refreshing and retrying once.")
            self._access_token_expires_at = 0
            self.ensure_authenticated()
            headers["Authorization"] = f"Bearer {self._access_token}"
            r = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if not r.ok:
            raise TadoApiError(f"{method} {path} -> {r.status_code} {r.text}")
        if r.status_code == 204 or not r.text:
            return None
        return r.json()

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def get_home_id(self) -> int:
        me = self.get_me()
        homes = me.get("homes", [])
        if not homes:
            raise TadoApiError("No homes found on this Tado account.")
        return int(homes[0]["id"])

    def get_zones(self, home_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/homes/{home_id}/zones")

    def find_heating_zone_id(self, home_id: int) -> int:
        zones = self.get_zones(home_id)
        for z in zones:
            if z.get("type") == "HEATING":
                return int(z["id"])
        raise TadoApiError("No HEATING zone found in this home.")

    def get_zone_state(self, home_id: int, zone_id: int) -> dict[str, Any]:
        return self._request("GET", f"/homes/{home_id}/zones/{zone_id}/state")

    def set_heating_on(
        self,
        home_id: int,
        zone_id: int,
        target_celsius: float,
        termination: str = "MANUAL",
        timer_seconds: Optional[int] = None,
    ) -> None:
        """
        Turn heating on by creating a manual overlay at the given target.

        termination:
          MANUAL           - overlay stays until changed
          NEXT_TIME_BLOCK  - overlay ends at next scheduled change (uses TADO_MODE)
          TIMER            - overlay ends after timer_seconds (must be provided)
        """
        termination_payload: dict[str, Any]
        if termination == "MANUAL":
            termination_payload = {"type": "MANUAL"}
        elif termination == "NEXT_TIME_BLOCK":
            termination_payload = {"type": "TADO_MODE"}
        elif termination == "TIMER":
            if not timer_seconds:
                raise ValueError("timer_seconds required when termination=TIMER")
            termination_payload = {"type": "TIMER", "durationInSeconds": int(timer_seconds)}
        else:
            raise ValueError(f"Unknown termination type: {termination}")

        payload = {
            "setting": {
                "type": "HEATING",
                "power": "ON",
                "temperature": {"celsius": float(target_celsius)},
            },
            "termination": termination_payload,
        }
        self._request("PUT", f"/homes/{home_id}/zones/{zone_id}/overlay", json=payload)
        log.info(
            "Heating ON for zone %s @ %.1f°C (termination=%s)",
            zone_id, target_celsius, termination
        )

    def set_heating_off(self, home_id: int, zone_id: int, termination: str = "MANUAL") -> None:
        """Turn heating off via a manual overlay."""
        termination_payload = (
            {"type": "MANUAL"} if termination == "MANUAL" else {"type": "TADO_MODE"}
        )
        payload = {
            "setting": {"type": "HEATING", "power": "OFF"},
            "termination": termination_payload,
        }
        self._request("PUT", f"/homes/{home_id}/zones/{zone_id}/overlay", json=payload)
        log.info("Heating OFF for zone %s (termination=%s)", zone_id, termination)

    def clear_overlay(self, home_id: int, zone_id: int) -> None:
        """Remove any manual overlay, returning the zone to Tado's own schedule."""
        self._request("DELETE", f"/homes/{home_id}/zones/{zone_id}/overlay")
        log.info("Cleared overlay for zone %s (back to Tado schedule)", zone_id)

    def get_indoor_temperature(self, home_id: int, zone_id: int) -> Optional[float]:
        """
        Return the current indoor temperature for a zone from Tado's zone state,
        or None if it cannot be read.

        Tado's zone state response includes:
          sensorDataPoints.insideTemperature.celsius
        """
        try:
            state = self.get_zone_state(home_id, zone_id)
            sensor_points = state.get("sensorDataPoints", {})
            inside = sensor_points.get("insideTemperature", {})
            celsius = inside.get("celsius")
            if celsius is not None:
                return float(celsius)
            log.warning("Tado zone state had no insideTemperature.celsius field.")
            return None
        except Exception as e:
            log.warning("Failed to read indoor temperature from Tado: %s", e)
            return None
