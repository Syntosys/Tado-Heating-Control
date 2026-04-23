"""
Thread-safe shared state between the control loop and the HTTP API.
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .decision import HeatingState


@dataclass
class SensorReading:
    sensor_id: str
    location: str          # "indoor" or "outdoor"
    temperature_c: float
    fetched_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "location": self.location,
            "temperature_c": self.temperature_c,
            "fetched_at": self.fetched_at,
            "age_seconds": max(0.0, time.time() - self.fetched_at),
        }


@dataclass
class Snapshot:
    last_loop_at: Optional[float] = None         # unix ts of last successful tick
    outdoor_temp_c: Optional[float] = None
    outdoor_fetched_at: Optional[float] = None
    outdoor_source: Optional[str] = None         # "sensor" | "weather" | None
    indoor_temp_c: Optional[float] = None
    indoor_fetched_at: Optional[float] = None
    indoor_source: Optional[str] = None          # "sensor" | "tado" | None
    active_window_name: Optional[str] = None
    threshold_c: Optional[float] = None
    desired_state: str = HeatingState.UNKNOWN.value
    commanded_state: str = HeatingState.UNKNOWN.value  # what we last sent to Tado
    last_state_change_at: Optional[float] = None
    last_reason: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
    tado_zone_id: Optional[int] = None
    tado_home_id: Optional[int] = None
    # Override fields
    override_mode: Optional[str] = None          # "on" | "off" | None (None = auto)
    override_expiry_at: Optional[float] = None   # unix ts when override expires
    last_tado_command_at: Optional[float] = None # last time we actually sent a cmd to Tado
    next_transition: Optional[str] = None        # human-readable description
    # Individual sensor readings, keyed by sensor_id. Rebuilt on snapshot().
    sensors: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["now"] = time.time()
        d["override_active"] = self.override_mode is not None and (
            self.override_expiry_at is None or time.time() < self.override_expiry_at
        )
        return d


_VALID_LOCATIONS = ("indoor", "outdoor")
_VALID_AGGREGATES = ("mean", "max", "min")


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._sensors: dict[str, SensorReading] = {}

    def update(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                if hasattr(self._snap, k):
                    setattr(self._snap, k, v)

    # ------------------------------------------------------------------
    # Sensor readings
    # ------------------------------------------------------------------
    def record_sensor(self, sensor_id: str, temperature_c: float, location: str) -> None:
        """Record a reading from a named sensor at a given location."""
        if location not in _VALID_LOCATIONS:
            raise ValueError(f"location must be one of {_VALID_LOCATIONS}")
        sid = (sensor_id or "default").strip() or "default"
        with self._lock:
            self._sensors[sid] = SensorReading(
                sensor_id=sid,
                location=location,
                temperature_c=float(temperature_c),
                fetched_at=time.time(),
            )

    def record_indoor(self, temp_c: float) -> None:
        """Back-compat shim: older ESP32 sketches POST without a location."""
        self.record_sensor("default", temp_c, "indoor")

    def fresh_sensors(self, location: str, max_age_seconds: float) -> list[SensorReading]:
        """Return a list of readings at `location` that are fresher than max age."""
        cutoff = time.time() - max_age_seconds
        with self._lock:
            return [
                r for r in self._sensors.values()
                if r.location == location and r.fetched_at >= cutoff
            ]

    def aggregate_reading(
        self,
        location: str,
        max_age_seconds: float,
        mode: str = "mean",
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Combine all fresh readings at `location` into a single value.
        Returns (temp_c, most_recent_fetched_at) or (None, None) if there
        are no fresh readings.
        """
        readings = self.fresh_sensors(location, max_age_seconds)
        if not readings:
            return None, None
        temps = [r.temperature_c for r in readings]
        if mode == "max":
            temp = max(temps)
        elif mode == "min":
            temp = min(temps)
        elif mode == "mean":
            temp = statistics.fmean(temps)
        else:
            raise ValueError(f"aggregate mode must be one of {_VALID_AGGREGATES}")
        most_recent = max(r.fetched_at for r in readings)
        return temp, most_recent

    def indoor_reading(self) -> tuple[Optional[float], Optional[float]]:
        """Back-compat: returns the most recent raw indoor reading (any id)."""
        with self._lock:
            indoor = [r for r in self._sensors.values() if r.location == "indoor"]
        if not indoor:
            return None, None
        latest = max(indoor, key=lambda r: r.fetched_at)
        return latest.temperature_c, latest.fetched_at

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            # Flatten sensors into the snapshot for the UI/clients.
            self._snap.sensors = {
                sid: r.to_dict() for sid, r in self._sensors.items()
            }
            return self._snap.to_dict()

    # ------------------------------------------------------------------
    # Override management
    # ------------------------------------------------------------------
    def set_override(self, mode: str, expiry_minutes: int) -> None:
        with self._lock:
            self._snap.override_mode = mode
            self._snap.override_expiry_at = time.time() + expiry_minutes * 60

    def clear_override(self) -> None:
        with self._lock:
            self._snap.override_mode = None
            self._snap.override_expiry_at = None

    def get_override(self) -> Optional[str]:
        with self._lock:
            mode = self._snap.override_mode
            expiry = self._snap.override_expiry_at
        if mode is None:
            return None
        if expiry is not None and time.time() >= expiry:
            self.clear_override()
            return None
        return mode
