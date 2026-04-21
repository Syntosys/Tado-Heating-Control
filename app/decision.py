"""
Decision engine for the heating controller.

Given:
  - current time
  - active schedule window (or None)
  - outdoor temperature (and optionally indoor temperature from a sensor)
  - current desired state (last commanded on/off)

...decide whether heating should be ON or OFF, applying hysteresis so we don't
rapid-cycle around the threshold.

The engine is stateless w.r.t. the Tado API — it just emits a Decision, and the
orchestrator turns that into API calls respecting min_state_change_interval.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .schedule import Window, active_window

log = logging.getLogger(__name__)


class HeatingState(str, Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


@dataclass
class DecisionInputs:
    now: dt.datetime
    outdoor_temp_c: Optional[float]
    indoor_temp_c: Optional[float]  # None if no fresh sensor reading
    current_state: HeatingState     # what we last commanded
    schedule_windows: list[Window]
    hysteresis_c: float
    indoor_threshold_c: Optional[float]  # from config.sensor.indoor_threshold_celsius


@dataclass
class Decision:
    desired_state: HeatingState
    reason: str
    active_window_name: Optional[str] = None
    threshold_used_c: Optional[float] = None
    extras: dict = field(default_factory=dict)


def decide(inputs: DecisionInputs) -> Decision:
    window = active_window(inputs.schedule_windows, inputs.now)

    # 1. Outside any scheduled window -> force off.
    if window is None:
        return Decision(
            desired_state=HeatingState.OFF,
            reason="outside scheduled window",
        )

    # 2. Inside a window. Decide based on temperature.
    # Priority: indoor sensor (if fresh) overrides outdoor reading for comfort control.
    if inputs.indoor_temp_c is not None and inputs.indoor_threshold_c is not None:
        threshold = inputs.indoor_threshold_c
        temp = inputs.indoor_temp_c
        source = "indoor"
    elif inputs.outdoor_temp_c is not None:
        threshold = window.outdoor_threshold_celsius
        temp = inputs.outdoor_temp_c
        source = "outdoor"
    else:
        # No temperature data at all — safest to keep current state rather than flap.
        return Decision(
            desired_state=inputs.current_state if inputs.current_state != HeatingState.UNKNOWN else HeatingState.OFF,
            reason="no temperature data available",
            active_window_name=window.name,
        )

    # Hysteresis:
    #   If currently OFF (or UNKNOWN), turn ON when temp < threshold - hysteresis
    #   If currently ON, stay ON until temp > threshold + hysteresis
    lower = threshold - inputs.hysteresis_c
    upper = threshold + inputs.hysteresis_c

    if inputs.current_state == HeatingState.ON:
        if temp > upper:
            desired = HeatingState.OFF
            reason = f"{source} temp {temp:.1f}°C > threshold+hyst ({upper:.1f}°C)"
        else:
            desired = HeatingState.ON
            reason = f"holding ON: {source} temp {temp:.1f}°C within hysteresis band"
    else:
        if temp < lower:
            desired = HeatingState.ON
            reason = f"{source} temp {temp:.1f}°C < threshold-hyst ({lower:.1f}°C)"
        else:
            desired = HeatingState.OFF
            reason = f"holding OFF: {source} temp {temp:.1f}°C >= threshold-hyst ({lower:.1f}°C)"

    return Decision(
        desired_state=desired,
        reason=reason,
        active_window_name=window.name,
        threshold_used_c=threshold,
        extras={"source": source, "observed_temp_c": temp},
    )
