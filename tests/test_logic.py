"""Quick tests for the schedule + decision engine."""

import datetime as dt
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schedule import parse_schedule, active_window
from app.decision import decide, DecisionInputs, HeatingState


def test_schedule_parsing():
    windows = parse_schedule([
        {"name": "morn", "days": "weekdays", "start": "06:30", "end": "09:00", "outdoor_threshold_celsius": 12},
        {"name": "eve", "days": "all", "start": "18:00", "end": "22:00", "outdoor_threshold_celsius": 14},
    ])
    assert len(windows) == 2
    # Monday 07:00 -> morning window
    mon_morning = dt.datetime(2025, 4, 21, 7, 0)  # a Monday
    w = active_window(windows, mon_morning)
    assert w is not None and w.name == "morn"
    # Sunday 07:00 -> nothing (weekday only)
    sun_morning = dt.datetime(2025, 4, 20, 7, 0)  # a Sunday
    assert active_window(windows, sun_morning) is None
    # Sunday 19:00 -> evening window (days: all)
    sun_eve = dt.datetime(2025, 4, 20, 19, 0)
    w = active_window(windows, sun_eve)
    assert w is not None and w.name == "eve"
    # Monday 14:00 -> between windows
    assert active_window(windows, dt.datetime(2025, 4, 21, 14, 0)) is None
    print("  schedule parsing OK")


def test_outside_window_always_off():
    windows = parse_schedule([
        {"name": "morn", "days": "weekdays", "start": "06:30", "end": "09:00", "outdoor_threshold_celsius": 12},
    ])
    inputs = DecisionInputs(
        now=dt.datetime(2025, 4, 21, 14, 0),  # outside window
        outdoor_temp_c=0.0,  # freezing — but schedule trumps
        indoor_temp_c=None,
        current_state=HeatingState.ON,
        schedule_windows=windows,
        hysteresis_c=0.5,
        indoor_threshold_c=None,
    )
    d = decide(inputs)
    assert d.desired_state == HeatingState.OFF
    assert "outside" in d.reason
    print("  outside window -> OFF")


def test_threshold_on():
    windows = parse_schedule([
        {"name": "morn", "days": "weekdays", "start": "06:30", "end": "09:00", "outdoor_threshold_celsius": 12},
    ])
    inputs = DecisionInputs(
        now=dt.datetime(2025, 4, 21, 7, 0),
        outdoor_temp_c=5.0,  # well below threshold
        indoor_temp_c=None,
        current_state=HeatingState.OFF,
        schedule_windows=windows,
        hysteresis_c=0.5,
        indoor_threshold_c=None,
    )
    d = decide(inputs)
    assert d.desired_state == HeatingState.ON
    print("  cold outside + in window -> ON")


def test_hysteresis_no_flap():
    windows = parse_schedule([
        {"name": "morn", "days": "weekdays", "start": "06:30", "end": "09:00", "outdoor_threshold_celsius": 12},
    ])
    # Currently ON, outdoor temp is exactly at threshold — should stay ON (within hyst band).
    inputs = DecisionInputs(
        now=dt.datetime(2025, 4, 21, 7, 0),
        outdoor_temp_c=12.0,
        indoor_temp_c=None,
        current_state=HeatingState.ON,
        schedule_windows=windows,
        hysteresis_c=0.5,
        indoor_threshold_c=None,
    )
    d = decide(inputs)
    assert d.desired_state == HeatingState.ON, f"expected ON, got {d.desired_state} ({d.reason})"

    # Now at threshold + hyst + a bit -> flip OFF.
    inputs2 = DecisionInputs(
        now=dt.datetime(2025, 4, 21, 7, 0),
        outdoor_temp_c=12.6,
        indoor_temp_c=None,
        current_state=HeatingState.ON,
        schedule_windows=windows,
        hysteresis_c=0.5,
        indoor_threshold_c=None,
    )
    d2 = decide(inputs2)
    assert d2.desired_state == HeatingState.OFF, f"expected OFF, got {d2.desired_state} ({d2.reason})"
    print("  hysteresis prevents flapping")


def test_indoor_sensor_overrides_outdoor():
    windows = parse_schedule([
        {"name": "morn", "days": "weekdays", "start": "06:30", "end": "09:00", "outdoor_threshold_celsius": 12},
    ])
    # Outdoor says "would be OFF" (warm), but indoor sensor says "cold" -> ON
    inputs = DecisionInputs(
        now=dt.datetime(2025, 4, 21, 7, 0),
        outdoor_temp_c=20.0,  # very warm outside
        indoor_temp_c=17.0,   # cold inside
        current_state=HeatingState.OFF,
        schedule_windows=windows,
        hysteresis_c=0.5,
        indoor_threshold_c=19.0,
    )
    d = decide(inputs)
    assert d.desired_state == HeatingState.ON
    assert d.extras["source"] == "indoor"
    print("  indoor sensor overrides outdoor")


if __name__ == "__main__":
    test_schedule_parsing()
    test_outside_window_always_off()
    test_threshold_on()
    test_hysteresis_no_flap()
    test_indoor_sensor_overrides_outdoor()
    print("\nAll tests passed ✓")
