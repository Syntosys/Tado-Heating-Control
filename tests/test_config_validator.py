"""Tests for app.config_validator."""

from __future__ import annotations

import textwrap

import pytest

from app.config_validator import validate, validate_file


def _valid_cfg() -> dict:
    return {
        "mode": "primary",
        "location": {"latitude": 51.5, "longitude": -0.1, "name": "London"},
        "weather": {"provider": "open-meteo", "poll_interval_seconds": 600},
        "tado": {"token_file": "/var/lib/heating-brain/tado_refresh_token"},
        "control": {
            "heat_on_target_celsius": 20.0,
            "off_behavior": "off",
            "on_overlay_termination": "MANUAL",
        },
        "http": {"host": "0.0.0.0", "port": 8423, "pin": ""},
        "schedule": [
            {
                "name": "Weekday mornings",
                "days": "weekdays",
                "start": "06:30",
                "end": "09:00",
                "indoor_on_celsius": 18.0,
                "outdoor_on_celsius": 15.0,
                "indoor_off_celsius": 20.0,
                "outdoor_off_celsius": 17.0,
            }
        ],
        "logging": {"level": "INFO", "file": "/var/log/heating-brain/heating-brain.log"},
    }


def test_valid_config_returns_no_errors():
    assert validate(_valid_cfg()) == []


def test_missing_required_top_level_keys():
    cfg = _valid_cfg()
    del cfg["schedule"]
    del cfg["tado"]
    errors = validate(cfg)
    assert any("schedule" in e and "missing" in e for e in errors)
    assert any("tado" in e and "missing" in e for e in errors)


def test_bad_day_name():
    cfg = _valid_cfg()
    cfg["schedule"][0]["days"] = ["mon", "funday"]
    errors = validate(cfg)
    assert any("funday" in e for e in errors)


def test_bad_time_format():
    cfg = _valid_cfg()
    cfg["schedule"][0]["start"] = "6 AM"
    errors = validate(cfg)
    assert any("schedule[0].start" in e for e in errors)


def test_out_of_range_time():
    cfg = _valid_cfg()
    cfg["schedule"][0]["end"] = "25:00"
    errors = validate(cfg)
    assert any("schedule[0].end" in e and "range" in e for e in errors)


def test_swapped_on_off_thresholds_indoor():
    cfg = _valid_cfg()
    cfg["schedule"][0]["indoor_on_celsius"] = 22.0
    cfg["schedule"][0]["indoor_off_celsius"] = 18.0
    errors = validate(cfg)
    assert any("indoor_off_celsius" in e and "indoor_on_celsius" in e for e in errors)


def test_client_mode_requires_primary_url():
    cfg = _valid_cfg()
    cfg["mode"] = "client"
    errors = validate(cfg)
    assert any("primary_url" in e for e in errors)
    cfg["primary_url"] = "http://192.168.1.42:8423"
    assert validate(cfg) == []


def test_invalid_mode():
    cfg = _valid_cfg()
    cfg["mode"] = "follower"
    errors = validate(cfg)
    assert any("mode" in e for e in errors)


def test_pin_must_be_four_digits():
    cfg = _valid_cfg()
    cfg["http"]["pin"] = "12"
    assert any("http.pin" in e for e in validate(cfg))
    cfg["http"]["pin"] = "abcd"
    assert any("http.pin" in e for e in validate(cfg))
    cfg["http"]["pin"] = "1234"
    assert validate(cfg) == []


def test_port_out_of_range():
    cfg = _valid_cfg()
    cfg["http"]["port"] = 70000
    assert any("http.port" in e for e in validate(cfg))


def test_empty_schedule_rejected():
    cfg = _valid_cfg()
    cfg["schedule"] = []
    assert any("schedule" in e for e in validate(cfg))


def test_legacy_outdoor_threshold_accepted():
    cfg = _valid_cfg()
    win = cfg["schedule"][0]
    for k in (
        "indoor_on_celsius", "outdoor_on_celsius",
        "indoor_off_celsius", "outdoor_off_celsius",
    ):
        del win[k]
    win["outdoor_threshold_celsius"] = 15.0
    assert validate(cfg) == []


def test_validate_file_yaml_parse_error_from_actual_incident(tmp_path):
    """The real /etc/heating-brain/config.yaml from the May 2026 outage:
    a list of schedule items appended at top-level under `logging:` with no
    parent key. Must be rejected, not parsed as something weird."""
    broken = textwrap.dedent(
        """\
        mode: primary
        location:
          latitude: 51.5
          longitude: -0.1
        weather: {provider: open-meteo}
        tado: {token_file: /tmp/t}
        control: {heat_on_target_celsius: 20.0}
        http: {port: 8423}
        logging:
          level: INFO
          file: /var/log/heating-brain/heating-brain.log
        - days:
          - mon
          end: '09:00'
          indoor_off_celsius: 20.0
          indoor_on_celsius: 18.0
          name: Stray
          outdoor_off_celsius: 17.0
          outdoor_on_celsius: 15.0
          start: '06:30'
        schedule:
          - name: real
            days: weekdays
            start: "06:30"
            end:   "09:00"
            indoor_on_celsius:   18.0
            outdoor_on_celsius:  15.0
            indoor_off_celsius:  20.0
            outdoor_off_celsius: 17.0
        """
    )
    p = tmp_path / "config.yaml"
    p.write_text(broken, encoding="utf-8")
    errors = validate_file(p)
    assert errors
    assert any("YAML parse error" in e for e in errors)


def test_validate_file_missing(tmp_path):
    errors = validate_file(tmp_path / "nope.yaml")
    assert errors and "not found" in errors[0]


def test_example_config_is_valid():
    """The shipped config.example.yaml must always validate cleanly."""
    from pathlib import Path
    example = Path(__file__).resolve().parent.parent / "app" / "config.example.yaml"
    assert validate_file(example) == []
