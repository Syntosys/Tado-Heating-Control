"""
Config schema + sanity validator.

Two entry points:
  - validate(cfg)             -> list[str] of human-readable errors (empty = OK).
  - validate_file(path)       -> list[str], includes YAML parse errors.

CLI: ``python -m app.config_validator <path>`` exits 0 if valid, 1 otherwise
and prints each error on its own line to stderr. Used by the systemd
ExecStartPre hook and by config_writer before any atomic replace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
VALID_DAY_GROUPS = {"all", "weekdays", "weekends"}
VALID_MODES = {"primary", "client"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_OFF_BEHAVIOR = {"off", "auto"}
VALID_ON_OVERLAY = {"MANUAL", "NEXT_TIME_BLOCK", "TIMER"}
VALID_AGGREGATES = {"mean", "min", "max"}

REQUIRED_TOP_LEVEL = ["location", "weather", "tado", "control", "http", "schedule"]
SCHEDULE_WINDOW_FIELDS = [
    "indoor_on_celsius", "outdoor_on_celsius",
    "indoor_off_celsius", "outdoor_off_celsius",
]


def _is_mapping(x: Any) -> bool:
    return isinstance(x, dict)


def _check_time(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{path}: must be a HH:MM string, got {type(value).__name__}")
        return
    parts = value.split(":")
    if len(parts) != 2:
        errors.append(f"{path}: must be HH:MM, got {value!r}")
        return
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        errors.append(f"{path}: HH and MM must be integers, got {value!r}")
        return
    if not (0 <= h <= 23 and 0 <= m <= 59):
        errors.append(f"{path}: out of range, got {value!r}")


def _check_days(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        k = value.lower().strip()
        if k not in VALID_DAY_GROUPS and k not in VALID_DAYS:
            errors.append(
                f"{path}: must be one of {sorted(VALID_DAY_GROUPS)} or a weekday name, got {value!r}"
            )
        return
    if isinstance(value, list):
        if not value:
            errors.append(f"{path}: empty day list")
            return
        for i, item in enumerate(value):
            if not isinstance(item, str) or item.lower().strip() not in VALID_DAYS:
                errors.append(f"{path}[{i}]: not a valid day name, got {item!r}")
        return
    errors.append(f"{path}: must be a string or list of weekday names, got {type(value).__name__}")


def _check_float(d: dict, key: str, parent: str, errors: list[str]) -> Any:
    if key not in d:
        errors.append(f"{parent}.{key}: missing")
        return None
    try:
        return float(d[key])
    except (TypeError, ValueError):
        errors.append(f"{parent}.{key}: must be a number, got {d[key]!r}")
        return None


def _validate_schedule_window(idx: int, item: Any, errors: list[str]) -> None:
    path = f"schedule[{idx}]"
    if not _is_mapping(item):
        errors.append(f"{path}: must be a mapping, got {type(item).__name__}")
        return

    if "days" not in item:
        errors.append(f"{path}.days: missing")
    else:
        _check_days(item["days"], f"{path}.days", errors)

    for tkey in ("start", "end"):
        if tkey not in item:
            errors.append(f"{path}.{tkey}: missing")
        else:
            _check_time(item[tkey], f"{path}.{tkey}", errors)

    has_new = all(k in item for k in SCHEDULE_WINDOW_FIELDS)
    has_old = "outdoor_threshold_celsius" in item
    if not (has_new or has_old):
        errors.append(
            f"{path}: must define indoor_on_celsius/outdoor_on_celsius/"
            f"indoor_off_celsius/outdoor_off_celsius (or legacy outdoor_threshold_celsius)"
        )
        return

    if has_new:
        indoor_on = _check_float(item, "indoor_on_celsius", path, errors)
        outdoor_on = _check_float(item, "outdoor_on_celsius", path, errors)
        indoor_off = _check_float(item, "indoor_off_celsius", path, errors)
        outdoor_off = _check_float(item, "outdoor_off_celsius", path, errors)
        if None not in (indoor_on, indoor_off) and indoor_off < indoor_on:
            errors.append(
                f"{path}: indoor_off_celsius ({indoor_off}) must be >= "
                f"indoor_on_celsius ({indoor_on}) — otherwise heating oscillates"
            )
        if None not in (outdoor_on, outdoor_off) and outdoor_off < outdoor_on:
            errors.append(
                f"{path}: outdoor_off_celsius ({outdoor_off}) must be >= "
                f"outdoor_on_celsius ({outdoor_on})"
            )


def _validate_location(cfg: dict, errors: list[str]) -> None:
    loc = cfg.get("location")
    if not _is_mapping(loc):
        errors.append("location: must be a mapping")
        return
    for key in ("latitude", "longitude"):
        if key not in loc:
            errors.append(f"location.{key}: missing")
            continue
        try:
            float(loc[key])
        except (TypeError, ValueError):
            errors.append(f"location.{key}: must be a number, got {loc[key]!r}")


def _validate_tado(cfg: dict, errors: list[str]) -> None:
    tado = cfg.get("tado")
    if not _is_mapping(tado):
        errors.append("tado: must be a mapping")
        return
    if "token_file" not in tado or not isinstance(tado["token_file"], str):
        errors.append("tado.token_file: missing or not a string")


def _validate_control(cfg: dict, errors: list[str]) -> None:
    ctl = cfg.get("control")
    if not _is_mapping(ctl):
        errors.append("control: must be a mapping")
        return
    _check_float(ctl, "heat_on_target_celsius", "control", errors)
    if "off_behavior" in ctl and ctl["off_behavior"] not in VALID_OFF_BEHAVIOR:
        errors.append(
            f"control.off_behavior: must be one of {sorted(VALID_OFF_BEHAVIOR)}, "
            f"got {ctl['off_behavior']!r}"
        )
    if "on_overlay_termination" in ctl and ctl["on_overlay_termination"] not in VALID_ON_OVERLAY:
        errors.append(
            f"control.on_overlay_termination: must be one of {sorted(VALID_ON_OVERLAY)}, "
            f"got {ctl['on_overlay_termination']!r}"
        )


def _validate_http(cfg: dict, errors: list[str]) -> None:
    http = cfg.get("http")
    if not _is_mapping(http):
        errors.append("http: must be a mapping")
        return
    if "port" in http:
        try:
            p = int(http["port"])
            if not (1 <= p <= 65535):
                errors.append(f"http.port: out of range, got {p}")
        except (TypeError, ValueError):
            errors.append(f"http.port: must be an integer, got {http['port']!r}")
    pin = http.get("pin", "")
    if pin not in ("", None) and (not isinstance(pin, str) or not pin.isdigit() or len(pin) != 4):
        errors.append(f"http.pin: must be empty or a 4-digit string, got {pin!r}")


def _validate_sensor(cfg: dict, errors: list[str]) -> None:
    sensor = cfg.get("sensor")
    if sensor is None:
        return
    if not _is_mapping(sensor):
        errors.append("sensor: must be a mapping")
        return
    for key in ("indoor_aggregate", "outdoor_aggregate"):
        if key in sensor and sensor[key] not in VALID_AGGREGATES:
            errors.append(
                f"sensor.{key}: must be one of {sorted(VALID_AGGREGATES)}, got {sensor[key]!r}"
            )


def _validate_logging(cfg: dict, errors: list[str]) -> None:
    log_cfg = cfg.get("logging")
    if log_cfg is None:
        return
    if not _is_mapping(log_cfg):
        errors.append("logging: must be a mapping")
        return
    level = log_cfg.get("level")
    if level is not None and level not in VALID_LOG_LEVELS:
        errors.append(
            f"logging.level: must be one of {sorted(VALID_LOG_LEVELS)}, got {level!r}"
        )


def validate(cfg: Any) -> list[str]:
    """Return a list of error messages. Empty list means the config is valid."""
    errors: list[str] = []

    if not _is_mapping(cfg):
        return [f"top-level: must be a mapping, got {type(cfg).__name__}"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in cfg:
            errors.append(f"{key}: missing required top-level key")

    mode = cfg.get("mode")
    if mode is not None:
        if not isinstance(mode, str) or mode.strip().lower() not in VALID_MODES:
            errors.append(f"mode: must be one of {sorted(VALID_MODES)}, got {mode!r}")
        elif mode.strip().lower() == "client":
            url = cfg.get("primary_url")
            if not isinstance(url, str) or not url.strip():
                errors.append("primary_url: required when mode is 'client'")

    if "location" in cfg:
        _validate_location(cfg, errors)
    if "tado" in cfg:
        _validate_tado(cfg, errors)
    if "control" in cfg:
        _validate_control(cfg, errors)
    if "http" in cfg:
        _validate_http(cfg, errors)
    _validate_sensor(cfg, errors)
    _validate_logging(cfg, errors)

    sched = cfg.get("schedule")
    if sched is not None:
        if not isinstance(sched, list):
            errors.append(f"schedule: must be a list, got {type(sched).__name__}")
        elif not sched:
            errors.append("schedule: must contain at least one window")
        else:
            for i, item in enumerate(sched):
                _validate_schedule_window(i, item, errors)

    return errors


def validate_file(path: str | Path) -> list[str]:
    """Read a YAML file and return validation errors (includes parse errors)."""
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        return [f"{p}: file not found"]
    except yaml.YAMLError as e:
        return [f"{p}: YAML parse error: {e}"]
    return validate(cfg)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.config_validator <config.yaml>", file=sys.stderr)
        return 2
    errors = validate_file(sys.argv[1])
    if errors:
        print(f"Config validation failed ({len(errors)} error(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"Config OK: {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
