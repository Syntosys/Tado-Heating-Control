"""
Main control loop.

Every tick:
  1. Fetch outdoor weather (respecting its own cache interval).
  2. Read indoor sensor snapshot (if present and fresh).
  3. Run the decision engine.
  4. If the desired state differs from the commanded state AND the min-state-change
     interval has elapsed, push the new state to Tado.
  5. Update the shared snapshot for the HTTP API.

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
from .http_api import make_app
from .schedule import parse_schedule
from .state import SharedState
from .tado_client import TadoClient
from .weather import WeatherProvider

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config_path: str | Path):
        self.config = self._load_config(config_path)
        self._configure_logging()

        self.state = SharedState()

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
        self.hysteresis_c = float(ctrl["hysteresis_celsius"])
        self.min_state_change_interval = int(ctrl["min_state_change_interval_seconds"])
        self.off_behavior = ctrl.get("off_behavior", "off")
        self.on_termination = ctrl.get("on_overlay_termination", "MANUAL")
        self.timer_seconds = int(ctrl.get("timer_minutes", 120)) * 60

        sensor_cfg = self.config.get("sensor", {}) or {}
        self.sensor_enabled = bool(sensor_cfg.get("enabled", False))
        self.sensor_max_age = int(sensor_cfg.get("max_age_seconds", 600))
        self.indoor_threshold_c = (
            float(sensor_cfg["indoor_threshold_celsius"])
            if self.sensor_enabled and "indoor_threshold_celsius" in sensor_cfg
            else None
        )

        self._home_id: Optional[int] = None
        self._zone_id: Optional[int] = self.config["tado"].get("zone_id")
        self._commanded_state: HeatingState = HeatingState.UNKNOWN
        self._last_state_change_at: float = 0.0
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    @staticmethod
    def _load_config(path: str | Path) -> dict[str, Any]:
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
                # Don't crash if we can't write the log file — fall back to stderr.
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
        """
        Figure out whether Tado currently has heating on or off, so our initial
        commanded_state reflects reality rather than assuming UNKNOWN.
        """
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

    def _get_fresh_indoor(self) -> Optional[float]:
        if not self.sensor_enabled:
            return None
        temp, fetched_at = self.state.indoor_reading()
        if temp is None or fetched_at is None:
            return None
        if (time.time() - fetched_at) > self.sensor_max_age:
            return None
        return temp

    def _apply_decision(self, decision: Decision) -> None:
        self.state.update(
            desired_state=decision.desired_state.value,
            last_reason=decision.reason,
            active_window_name=decision.active_window_name,
            threshold_c=decision.threshold_used_c,
        )

        if decision.desired_state == self._commanded_state:
            return  # nothing to do

        # Min-state-change interval (skip the first transition so startup applies immediately).
        if self._last_state_change_at > 0:
            since = time.time() - self._last_state_change_at
            if since < self.min_state_change_interval:
                log.debug(
                    "Would change to %s but only %ds since last change (<%ds) — holding.",
                    decision.desired_state, int(since), self.min_state_change_interval,
                )
                return

        # Push to Tado.
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
            )
            log.info("Tado commanded -> %s (%s)", decision.desired_state.value, decision.reason)
        except Exception as e:
            log.error("Failed to push state %s to Tado: %s", decision.desired_state, e)
            self.state.update(last_error=f"tado: {e}", last_error_at=time.time())

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._fetch_weather_if_due()
        outdoor = self.weather.cached()
        indoor = self._get_fresh_indoor()

        inputs = DecisionInputs(
            now=dt.datetime.now(),
            outdoor_temp_c=outdoor.temperature_celsius if outdoor else None,
            indoor_temp_c=indoor,
            current_state=self._commanded_state,
            schedule_windows=self.schedule_windows,
            hysteresis_c=self.hysteresis_c,
            indoor_threshold_c=self.indoor_threshold_c,
        )
        decision = decide(inputs)
        log.debug("Decision: %s (%s)", decision.desired_state, decision.reason)
        self._apply_decision(decision)
        self.state.update(last_loop_at=time.time())

    def _control_loop(self) -> None:
        # Authenticate + resolve IDs before starting.
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
            # Sleep in small increments so stop is responsive.
            slept = 0.0
            while slept < self.tado_interval and not self._stop.is_set():
                time.sleep(1.0)
                slept += 1.0

    # ------------------------------------------------------------------
    def run(self) -> None:
        # Start HTTP API in a background thread.
        http_cfg = self.config.get("http", {}) or {}
        host = http_cfg.get("host", "0.0.0.0")
        port = int(http_cfg.get("port", 8423))
        sensor_token = (self.config.get("sensor", {}) or {}).get("token")
        app = make_app(self.state, sensor_token=sensor_token)
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
