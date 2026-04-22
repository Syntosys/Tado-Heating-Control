"""
Main control loop.

Every tick:
  1. Fetch outdoor weather (respecting its own cache interval).
  2. Fetch indoor temperature from Tado zone state.
     If a fresh ESP32 reading exists in shared state, prefer that over Tado's reading.
  3. Check for an active manual override — if one is set, apply it directly.
  4. Run the decision engine.
  5. If the desired state differs from the commanded state AND the min-state-change
     interval has elapsed, push the new state to Tado.
  6. Write a history sample.
  7. Update the shared snapshot for the HTTP API.

Tado calls are limited by the poll_interval_seconds config. With Auto-Assist
(20,000 calls/day) the default 60s poll uses ~1,440 calls/day, well within budget.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from .decision import Decision, DecisionInputs, HeatingState, decide
from .history import HistoryBuffer, HistorySample
from .http_api import make_app
from .schedule import active_window, parse_schedule
from .state import SharedState
from .tado_client import TadoClient
from .weather import WeatherProvider

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.config = self._load_config(self.config_path)
        self._configure_logging()

        self.state = SharedState()
        self.history = HistoryBuffer()

        loc = self.config["location"]
        self.weather = WeatherProvider(
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
        )
        self.weather_interval = int(self.config["weather"]["poll_interval_seconds"])
        self._weather_last_fetch: float = 0.0

        self.tado = TadoClient(token_file=self.config["tado"]["token_file"])
        self.tado_interval = int(self.config["tado"]["poll_interval_seconds"])

        self.schedule_windows = parse_schedule(self.config["schedule"])

        ctrl = self.config["control"]
        self.target_c = float(ctrl["heat_on_target_celsius"])
        self.min_state_change_interval = int(ctrl["min_state_change_interval_seconds"])
        self.off_behavior = ctrl.get("off_behavior", "off")
        self.on_termination = ctrl.get("on_overlay_termination", "MANUAL")
        self.timer_seconds = int(ctrl.get("timer_minutes", 120)) * 60

        # Deprecated config fields — warn but don't crash.
        if "hysteresis_celsius" in ctrl:
            log.warning(
                "control.hysteresis_celsius is deprecated and no longer used. "
                "Remove it from config.yaml — heating now uses per-window thresholds."
            )
        sensor_cfg = self.config.get("sensor", {}) or {}
        self.sensor_enabled = bool(sensor_cfg.get("enabled", False))
        self.sensor_max_age = int(sensor_cfg.get("max_age_seconds", 600))
        if "indoor_threshold_celsius" in sensor_cfg:
            log.warning(
                "sensor.indoor_threshold_celsius is deprecated and no longer used. "
                "Use per-window indoor_on_celsius / indoor_off_celsius instead."
            )

        http_cfg = self.config.get("http", {}) or {}
        self.override_expiry_minutes = int(http_cfg.get("override_expiry_minutes", 120))

        self._home_id: Optional[int] = None
        self._zone_id: Optional[int] = self.config["tado"].get("zone_id")
        self._commanded_state: HeatingState = HeatingState.UNKNOWN
        self._last_state_change_at: float = 0.0
        self._last_active_window_name: Optional[str] = None
        self._stop = threading.Event()
        self._wake = threading.Event()  # set by HTTP handlers to nudge the loop immediately

    # ------------------------------------------------------------------
    @staticmethod
    def _load_config(path: Path) -> dict[str, Any]:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _configure_logging(self) -> None:
        lcfg = self.config.get("logging", {}) or {}
        level = getattr(logging, lcfg.get("level", "INFO").upper(), logging.INFO)
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if lcfg.get("file"):
            try:
                logfile = Path(lcfg["file"])
                logfile.parent.mkdir(parents=True, exist_ok=True)
                handlers.append(logging.FileHandler(logfile))
            except OSError as e:
                print(f"Couldn't open log file {lcfg['file']}: {e}", flush=True)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers,
            force=True,
        )

    # ------------------------------------------------------------------
    def _ensure_tado_ids(self) -> None:
        if self._home_id is None:
            self._home_id = self.tado.get_home_id()
            self.state.update(tado_home_id=self._home_id)
            log.info("Tado home id: %s", self._home_id)
        if self._zone_id is None:
            self._zone_id = self.tado.find_heating_zone_id(self._home_id)
            self.state.update(tado_zone_id=self._zone_id)
            log.info("Tado heating zone id (auto-detected): %s", self._zone_id)
        else:
            self.state.update(tado_zone_id=self._zone_id)

    def _read_current_tado_state(self) -> HeatingState:
        try:
            s = self.tado.get_zone_state(self._home_id, self._zone_id)
            setting = s.get("setting", {})
            power = setting.get("power")
            if power == "ON":
                return HeatingState.ON
            if power == "OFF":
                return HeatingState.OFF
        except Exception as e:
            log.warning("Couldn't read initial Tado state: %s", e)
        return HeatingState.UNKNOWN

    # ------------------------------------------------------------------
    def _fetch_weather_if_due(self) -> None:
        if time.time() - self._weather_last_fetch < self.weather_interval:
            return
        try:
            r = self.weather.fetch()
            self._weather_last_fetch = time.time()
            self.state.update(
                outdoor_temp_c=r.temperature_celsius,
                outdoor_fetched_at=r.fetched_at,
            )
        except Exception as e:
            log.warning("Weather fetch failed: %s", e)
            self.state.update(last_error=f"weather: {e}", last_error_at=time.time())

    def _get_indoor_temp(self) -> Optional[float]:
        """
        Return the best available indoor temperature reading.
        Prefer a fresh ESP32 reading (posted to /sensor), fall back to Tado's
        zone state reading.
        """
        # Prefer fresh ESP32 reading if the sensor is enabled and reading is recent.
        if self.sensor_enabled:
            temp, fetched_at = self.state.indoor_reading()
            if temp is not None and fetched_at is not None:
                if (time.time() - fetched_at) <= self.sensor_max_age:
                    return temp

        # Fall back to Tado's own indoor sensor reading.
        if self._home_id is not None and self._zone_id is not None:
            tado_indoor = self.tado.get_indoor_temperature(self._home_id, self._zone_id)
            if tado_indoor is not None:
                # Store it in shared state so the UI can show it.
                self.state.update(
                    indoor_temp_c=tado_indoor,
                    indoor_fetched_at=time.time(),
                )
                return tado_indoor

        return None

    def _apply_decision(self, decision: Decision) -> None:
        self.state.update(
            desired_state=decision.desired_state.value,
            last_reason=decision.reason,
            active_window_name=decision.active_window_name,
            threshold_c=decision.threshold_used_c,
        )

        if decision.desired_state == self._commanded_state:
            return  # nothing to do

        is_override = decision.extras.get("rule_fired") == "override"

        # Min-state-change interval (skip the first transition so startup applies immediately).
        # Manual overrides bypass this guard — user action should take effect now.
        if self._last_state_change_at > 0 and not is_override:
            since = time.time() - self._last_state_change_at
            if since < self.min_state_change_interval:
                log.debug(
                    "Would change to %s but only %ds since last change (<%ds) — holding.",
                    decision.desired_state, int(since), self.min_state_change_interval,
                )
                return

        try:
            if decision.desired_state == HeatingState.ON:
                self.tado.set_heating_on(
                    self._home_id,
                    self._zone_id,
                    target_celsius=self.target_c,
                    termination=self.on_termination,
                    timer_seconds=self.timer_seconds if self.on_termination == "TIMER" else None,
                )
            else:  # OFF
                if self.off_behavior == "auto":
                    self.tado.clear_overlay(self._home_id, self._zone_id)
                else:
                    self.tado.set_heating_off(self._home_id, self._zone_id, termination="MANUAL")
            self._commanded_state = decision.desired_state
            self._last_state_change_at = time.time()
            self.state.update(
                commanded_state=self._commanded_state.value,
                last_state_change_at=self._last_state_change_at,
                last_tado_command_at=self._last_state_change_at,
            )
            log.info("Tado commanded -> %s (%s)", decision.desired_state.value, decision.reason)
        except Exception as e:
            log.error("Failed to push state %s to Tado: %s", decision.desired_state, e)
            self.state.update(last_error=f"tado: {e}", last_error_at=time.time())

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._fetch_weather_if_due()
        outdoor = self.weather.cached()
        indoor = self._get_indoor_temp()

        outdoor_temp = outdoor.temperature_celsius if outdoor else None

        # Auto-clear manual override when a new schedule window just started.
        # Rationale: user pressed On/Off manually — that hold lasts until the
        # next natural scheduled transition, at which point the schedule takes
        # over again. Without this, an override would persist forever.
        now = dt.datetime.now()
        current_window = active_window(self.schedule_windows, now)
        current_window_name = current_window.name if current_window else None
        if (
            current_window_name is not None
            and current_window_name != self._last_active_window_name
            and self.state.get_override() is not None
        ):
            log.info(
                "Schedule window '%s' started — clearing manual override.",
                current_window_name,
            )
            self.state.clear_override()
        self._last_active_window_name = current_window_name

        # Check for active manual override — applies before decision engine.
        override = self.state.get_override()
        if override is not None:
            desired = HeatingState.ON if override == "on" else HeatingState.OFF
            reason = f"manual override: {override}"
            decision = Decision(
                desired_state=desired,
                reason=reason,
                extras={
                    "indoor_temp_c": indoor,
                    "outdoor_temp_c": outdoor_temp,
                    "rule_fired": "override",
                },
            )
        else:
            inputs = DecisionInputs(
                now=dt.datetime.now(),
                outdoor_temp_c=outdoor_temp,
                indoor_temp_c=indoor,
                current_state=self._commanded_state,
                schedule_windows=self.schedule_windows,
                # Pass zero/None for deprecated fields — will not trigger warnings
                # since they equal the defaults.
                hysteresis_c=0.0,
                indoor_threshold_c=None,
            )
            decision = decide(inputs)

        log.debug("Decision: %s (%s)", decision.desired_state, decision.reason)
        self._apply_decision(decision)
        self.state.update(last_loop_at=time.time())

        # Write history sample.
        snap = self.state.snapshot()
        self.history.add(HistorySample(
            ts=time.time(),
            indoor_temp_c=indoor,
            outdoor_temp_c=outdoor_temp,
            heating_on=(self._commanded_state == HeatingState.ON),
        ))

    def _control_loop(self) -> None:
        log.info("Starting up — resolving Tado auth and IDs.")
        self.tado.ensure_authenticated()
        self._ensure_tado_ids()
        self._commanded_state = self._read_current_tado_state()
        self.state.update(commanded_state=self._commanded_state.value)
        log.info("Initial Tado state: %s", self._commanded_state.value)

        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.exception("Tick failed: %s", e)
                self.state.update(last_error=str(e), last_error_at=time.time())
            # Sleep until next tick OR until woken by an HTTP action (override/etc).
            self._wake.wait(timeout=self.tado_interval)
            self._wake.clear()

    # ------------------------------------------------------------------
    def run(self) -> None:
        http_cfg = self.config.get("http", {}) or {}
        host = http_cfg.get("host", "0.0.0.0")
        port = int(http_cfg.get("port", 8423))
        sensor_token = (self.config.get("sensor", {}) or {}).get("token")
        pin = str(http_cfg.get("pin", "")) or None
        app = make_app(
            self.state,
            history=self.history,
            schedule_windows=self.schedule_windows,
            config_path=self.config_path,
            sensor_token=sensor_token,
            pin=pin,
            override_expiry_minutes=self.override_expiry_minutes,
            wake=self._wake,
        )
        api_thread = threading.Thread(
            target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
            daemon=True,
            name="http-api",
        )
        api_thread.start()
        log.info("HTTP API listening on %s:%d", host, port)

        try:
            self._control_loop()
        except KeyboardInterrupt:
            log.info("Interrupted — shutting down.")
            self._stop.set()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Tado heating brain")
    parser.add_argument(
        "--config", "-c",
        default="/etc/heating-brain/config.yaml",
        help="Path to config.yaml",
    )
    args = parser.parse_args()
    Orchestrator(args.config).run()


if __name__ == "__main__":
    main()
