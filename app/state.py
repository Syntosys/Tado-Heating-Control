"""
Thread-safe shared state between the control loop and the HTTP API.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .decision import HeatingState


@dataclass
class Snapshot:
    last_loop_at: Optional[float] = None         # unix ts of last successful tick
    outdoor_temp_c: Optional[float] = None
    outdoor_fetched_at: Optional[float] = None
    indoor_temp_c: Optional[float] = None
    indoor_fetched_at: Optional[float] = None
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Add human-friendly derivatives
        d["now"] = time.time()
        return d


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()

    def update(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self._snap, k, v)

    def record_indoor(self, temp_c: float) -> None:
        with self._lock:
            self._snap.indoor_temp_c = float(temp_c)
            self._snap.indoor_fetched_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snap.to_dict()

    def indoor_reading(self) -> tuple[Optional[float], Optional[float]]:
        """Return (temp_c, fetched_at) under lock."""
        with self._lock:
            return self._snap.indoor_temp_c, self._snap.indoor_fetched_at
