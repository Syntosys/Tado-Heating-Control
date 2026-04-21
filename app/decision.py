"""
Decision engine for the heating controller.

Given:
  - current time
  - active schedule window (or None)
  - outdoor temperature
  - indoor temperature (from Tado zone state, or ESP32 override if fresh)
  - current desired state (last commanded on/off)

Rules inside an active window:
  - Turn ON  when indoor_temp < window.indoor_on_celsius  AND outdoor_temp < window.outdoor_on_celsius
  - Turn OFF when indoor_temp > window.indoor_off_celsius AND outdoor_temp > window.outdoor_off_celsius
  - Otherwise: hold current state (natural deadband — no extra hysteresis needed)

Outside all windows -> OFF (unconditional).

If indoor OR outdoor reading is unavailable -> hold current state, log a warning.

The engine is stateless w.r.t. the Tado API — it emits a Decision, and the
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
    indoor_temp_c: Optional[float]
    current_state: HeatingState
    schedule_windows: list[Window]
    # Deprecated fields — tolerated but ignored; the four per-window thresholds take over.
    hysteresis_c: float = 0.0
    indoor_threshold_c: Optional[float] = None


@dataclass
class Decision:
    desired_state: HeatingState
    reason: str
    active_window_name: Optional[str] = None
    threshold_used_c: Optional[float] = None  # kept for API compat; set to outdoor_on value when ON
    extras: dict = field(default_factory=dict)


def decide(inputs: DecisionInputs) -> Decision:
    # Warn if deprecated fields are actively set (non-default values passed).
    if inputs.hysteresis_c != 0.0:
        log.warning(
            "DecisionInputs.hysteresis_c is deprecated and ignored. "
            "Use per-window indoor/outdoor thresholds instead."
        )
    if inputs.indoor_threshold_c is not None:
        log.warning(
            "DecisionInputs.indoor_threshold_c is deprecated and ignored. "
            "Use per-window indoor_on_celsius / indoor_off_celsius instead."
        )

    window = active_window(inputs.schedule_windows, inputs.now)

    # 1. Outside any scheduled window -> force off.
    if window is None:
        return Decision(
            desired_state=HeatingState.OFF,
            reason="outside scheduled window",
        )

    # 2. Inside a window — both temps required for the AND-rule logic.
    if inputs.indoor_temp_c is None or inputs.outdoor_temp_c is None:
        # Missing data — hold current state rather than flapping.
        hold_state = (
            inputs.current_state
            if inputs.current_state != HeatingState.UNKNOWN
            else HeatingState.OFF
        )
        missing = []
        if inputs.indoor_temp_c is None:
            missing.append("indoor")
        if inputs.outdoor_temp_c is None:
            missing.append("outdoor")
        log.warning("Missing temperature data (%s) — holding %s", ", ".join(missing), hold_state.value)
        return Decision(
            desired_state=hold_state,
            reason=f"missing {'+'.join(missing)} temp — holding current state",
            active_window_name=window.name,
            extras={
                "indoor_temp_c": inputs.indoor_temp_c,
                "outdoor_temp_c": inputs.outdoor_temp_c,
                "rule_fired": "hold_missing_data",
            },
        )

    indoor = inputs.indoor_temp_c
    outdoor = inputs.outdoor_temp_c

    # AND-rule: both conditions must be met to change state.
    turn_on = indoor < window.indoor_on_celsius and outdoor < window.outdoor_on_celsius
    turn_off = indoor > window.indoor_off_celsius and outdoor > window.outdoor_off_celsius

    if turn_on:
        desired = HeatingState.ON
        reason = (
            f"indoor {indoor:.1f}°C < {window.indoor_on_celsius:.1f}°C "
            f"AND outdoor {outdoor:.1f}°C < {window.outdoor_on_celsius:.1f}°C"
        )
        rule_fired = "turn_on"
    elif turn_off:
        desired = HeatingState.OFF
        reason = (
            f"indoor {indoor:.1f}°C > {window.indoor_off_celsius:.1f}°C "
            f"AND outdoor {outdoor:.1f}°C > {window.outdoor_off_celsius:.1f}°C"
        )
        rule_fired = "turn_off"
    else:
        # Neither full condition met — hold current state (deadband).
        desired = (
            inputs.current_state
            if inputs.current_state != HeatingState.UNKNOWN
            else HeatingState.OFF
        )
        reason = (
            f"deadband: indoor {indoor:.1f}°C, outdoor {outdoor:.1f}°C "
            f"(on<{window.indoor_on_celsius}/{window.outdoor_on_celsius}, "
            f"off>{window.indoor_off_celsius}/{window.outdoor_off_celsius})"
        )
        rule_fired = "hold_deadband"

    return Decision(
        desired_state=desired,
        reason=reason,
        active_window_name=window.name,
        threshold_used_c=window.outdoor_on_celsius,
        extras={
            "indoor_temp_c": indoor,
            "outdoor_temp_c": outdoor,
            "rule_fired": rule_fired,
        },
    )
