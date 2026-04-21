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
    # Override fields
    override_mode: Optional[str] = None          # "on" | "off" | None (None = auto)
    override_expiry_at: Optional[float] = None   # unix ts when override expires
    last_tado_command_at: Optional[float] = None # last time we actually sent a cmd to Tado
    next_transition: Optional[str] = None        # human-readable description

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Add human-friendly derivatives
        d["now"] = time.time()
        # Flatten override into a single field for the UI
        d["override_active"] = self.override_mode is not None and (
            self.override_expiry_at is None or time.time() < self.override_expiry_at
        )
        return d


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()

    def update(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                if hasattr(self._snap, k):
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

    # ------------------------------------------------------------------
    # Override management
    # ------------------------------------------------------------------
    def set_override(self, mode: str, expiry_minutes: int) -> None:
        """Set a manual override: mode is 'on' or 'off'."""
        with self._lock:
            self._snap.override_mode = mode
            self._snap.override_expiry_at = time.time() + expiry_minutes * 60

    def clear_override(self) -> None:
        """Clear any active manual override (resume auto)."""
        with self._lock:
            self._snap.override_mode = None
            self._snap.override_expiry_at = None

    def get_override(self) -> Optional[str]:
        """
        Return the active override mode ('on' or 'off'), or None if no
        active override (either not set or expired).
        """
        with self._lock:
            mode = self._snap.override_mode
            expiry = self._snap.override_expiry_at
        if mode is None:
            return None
        if expiry is not None and time.time() >= expiry:
            # Expired — clear it.
            self.clear_override()
            return None
        return mode
