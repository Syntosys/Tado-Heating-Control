"""Tests for schedule parsing and the four-threshold decision engine.

All tests are network-free. Run with:
    python tests/test_logic.py
"""

import datetime as dt
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schedule import parse_schedule, active_window
from app.decision import decide, DecisionInputs, HeatingState


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _windows_new():
    """Return two windows using the new four-threshold format."""
    return parse_schedule([
        {
            "name": "morn",
            "days": "weekdays",
            "start": "06:30",
            "end": "09:00",
            "indoor_on_celsius": 18.0,
            "outdoor_on_celsius": 15.0,
            "indoor_off_celsius": 20.0,
            "outdoor_off_celsius": 17.0,
        },
        {
            "name": "eve",
            "days": "all",
            "start": "18:00",
            "end": "22:00",
            "indoor_on_celsius": 17.0,
            "outdoor_on_celsius": 14.0,
            "indoor_off_celsius": 21.0,
            "outdoor_off_celsius": 16.0,
        },
    ])


def _inputs(windows, now, indoor, outdoor, current=HeatingState.OFF):
    return DecisionInputs(
        now=now,
        outdoor_temp_c=outdoor,
        indoor_temp_c=indoor,
        current_state=current,
        schedule_windows=windows,
        hysteresis_c=0.0,
        indoor_threshold_c=None,
    )


# ------------------------------------------------------------------
# Schedule parsing
# ------------------------------------------------------------------

def test_schedule_parsing_new_format():
    windows = _windows_new()
    assert len(windows) == 2
    mon_morning = dt.datetime(2025, 4, 21, 7, 0)   # Monday
    w = active_window(windows, mon_morning)
    assert w is not None and w.name == "morn"
    sun_morning = dt.datetime(2025, 4, 20, 7, 0)   # Sunday
    assert active_window(windows, sun_morning) is None
    sun_eve = dt.datetime(2025, 4, 20, 19, 0)
    w = active_window(windows, sun_eve)
    assert w is not None and w.name == "eve"
    assert active_window(windows, dt.datetime(2025, 4, 21, 14, 0)) is None
    print("  schedule parsing (new format) OK")


def test_schedule_parsing_legacy_migration():
    """Old outdoor_threshold_celsius field should parse with a warning, not crash."""
    windows = parse_schedule([
        {
            "name": "legacy",
            "days": "weekdays",
            "start": "06:30",
            "end": "09:00",
            "outdoor_threshold_celsius": 12.0,
        }
    ])
    assert len(windows) == 1
    w = windows[0]
    # Migration formula: outdoor_on = old_thresh, outdoor_off = old_thresh + 2
    assert w.outdoor_on_celsius == 12.0
    assert w.outdoor_off_celsius == 14.0
    assert w.indoor_on_celsius == 18.0
    assert w.indoor_off_celsius == 20.0
    print("  legacy schedule migration OK")


# ------------------------------------------------------------------
# Outside window -> OFF
# ------------------------------------------------------------------

def test_outside_window_always_off():
    windows = _windows_new()
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 14, 0), indoor=0.0, outdoor=0.0,
                       current=HeatingState.ON))
    assert d.desired_state == HeatingState.OFF
    assert "outside" in d.reason
    print("  outside window -> OFF")


# ------------------------------------------------------------------
# AND-rule: cold inside AND cold outside -> ON
# ------------------------------------------------------------------

def test_cold_cold_turns_on():
    windows = _windows_new()
    # indoor=16 < 18, outdoor=10 < 15 -> both ON conditions met
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=16.0, outdoor=10.0, current=HeatingState.OFF))
    assert d.desired_state == HeatingState.ON
    assert d.extras["rule_fired"] == "turn_on"
    print("  cold indoor + cold outdoor -> ON")


# ------------------------------------------------------------------
# AND-rule: warm inside AND warm outside -> OFF
# ------------------------------------------------------------------

def test_warm_warm_turns_off():
    windows = _windows_new()
    # indoor=21 > 20, outdoor=18 > 17 -> both OFF conditions met
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=21.0, outdoor=18.0, current=HeatingState.ON))
    assert d.desired_state == HeatingState.OFF
    assert d.extras["rule_fired"] == "turn_off"
    print("  warm indoor + warm outdoor -> OFF")


# ------------------------------------------------------------------
# Mixed: only ONE condition met -> hold (deadband)
# ------------------------------------------------------------------

def test_mixed_cold_inside_warm_outside_holds():
    windows = _windows_new()
    # indoor=16 < 18 (want ON), but outdoor=18 > 17 (want OFF)
    # Neither full condition met -> hold OFF
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=16.0, outdoor=18.0, current=HeatingState.OFF))
    assert d.desired_state == HeatingState.OFF
    assert d.extras["rule_fired"] == "hold_deadband"
    print("  cold indoor + warm outdoor (currently OFF) -> hold OFF")


def test_mixed_holds_current_on():
    windows = _windows_new()
    # indoor=19 between 18 and 20, outdoor=16 between 15 and 17
    # Neither turn_on nor turn_off conditions fully met -> hold current ON
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=19.0, outdoor=16.0, current=HeatingState.ON))
    assert d.desired_state == HeatingState.ON
    assert d.extras["rule_fired"] == "hold_deadband"
    print("  deadband temps (currently ON) -> hold ON")


# ------------------------------------------------------------------
# Missing data -> hold
# ------------------------------------------------------------------

def test_missing_indoor_holds():
    windows = _windows_new()
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=None, outdoor=10.0, current=HeatingState.OFF))
    assert d.desired_state == HeatingState.OFF
    assert "indoor" in d.reason
    assert d.extras["rule_fired"] == "hold_missing_data"
    print("  missing indoor -> hold")


def test_missing_outdoor_holds():
    windows = _windows_new()
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=16.0, outdoor=None, current=HeatingState.ON))
    assert d.desired_state == HeatingState.ON
    assert "outdoor" in d.reason
    assert d.extras["rule_fired"] == "hold_missing_data"
    print("  missing outdoor -> hold")


def test_missing_both_holds_with_unknown():
    windows = _windows_new()
    # When current is UNKNOWN and data is missing, should default to OFF
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=None, outdoor=None, current=HeatingState.UNKNOWN))
    assert d.desired_state == HeatingState.OFF
    print("  missing both + UNKNOWN state -> OFF")


# ------------------------------------------------------------------
# Extras fields present
# ------------------------------------------------------------------

def test_extras_populated():
    windows = _windows_new()
    d = decide(_inputs(windows, dt.datetime(2025, 4, 21, 7, 0),
                       indoor=16.0, outdoor=10.0, current=HeatingState.OFF))
    assert "indoor_temp_c" in d.extras
    assert "outdoor_temp_c" in d.extras
    assert "rule_fired" in d.extras
    print("  extras fields present")


if __name__ == "__main__":
    test_schedule_parsing_new_format()
    test_schedule_parsing_legacy_migration()
    test_outside_window_always_off()
    test_cold_cold_turns_on()
    test_warm_warm_turns_off()
    test_mixed_cold_inside_warm_outside_holds()
    test_mixed_holds_current_on()
    test_missing_indoor_holds()
    test_missing_outdoor_holds()
    test_missing_both_holds_with_unknown()
    test_extras_populated()
    print("\nAll tests passed.")
