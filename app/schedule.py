"""
Schedule engine.

A schedule is a list of windows, each with:
  - days: "all" | "weekdays" | "weekends" | list of ["mon","tue",...]
  - start, end: "HH:MM" local time
  - indoor_on_celsius:   turn ON when indoor < this AND outdoor < outdoor_on_celsius
  - outdoor_on_celsius:  see above
  - indoor_off_celsius:  turn OFF when indoor > this AND outdoor > outdoor_off_celsius
  - outdoor_off_celsius: see above

Migration: if only the old outdoor_threshold_celsius field is present, the parser
synthesises new thresholds with a warning so existing configs keep working.

Outside every window, heating is forced off.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = {"mon", "tue", "wed", "thu", "fri"}
WEEKENDS = {"sat", "sun"}


@dataclass
class Window:
    name: str
    days: set[str]  # subset of DAY_NAMES
    start: dt.time
    end: dt.time
    indoor_on_celsius: float    # turn ON when indoor < this AND outdoor < outdoor_on
    outdoor_on_celsius: float   # turn ON when outdoor < this AND indoor < indoor_on
    indoor_off_celsius: float   # turn OFF when indoor > this AND outdoor > outdoor_off
    outdoor_off_celsius: float  # turn OFF when outdoor > this AND indoor > indoor_off

    def is_active(self, now: dt.datetime) -> bool:
        day_name = DAY_NAMES[now.weekday()]
        if day_name not in self.days:
            return False
        t = now.time()
        if self.start <= self.end:
            return self.start <= t <= self.end
        # Wraps midnight (e.g. 22:00-06:00)
        return t >= self.start or t <= self.end

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "days": sorted(self.days, key=lambda d: DAY_NAMES.index(d)),
            "start": self.start.strftime("%H:%M"),
            "end": self.end.strftime("%H:%M"),
            "indoor_on_celsius": self.indoor_on_celsius,
            "outdoor_on_celsius": self.outdoor_on_celsius,
            "indoor_off_celsius": self.indoor_off_celsius,
            "outdoor_off_celsius": self.outdoor_off_celsius,
        }


def _parse_days(raw) -> set[str]:
    if isinstance(raw, str):
        k = raw.lower().strip()
        if k == "all":
            return set(DAY_NAMES)
        if k == "weekdays":
            return set(WEEKDAYS)
        if k == "weekends":
            return set(WEEKENDS)
        if k in DAY_NAMES:
            return {k}
        raise ValueError(f"Unknown days value: {raw!r}")
    if isinstance(raw, list):
        out = set()
        for item in raw:
            out |= _parse_days(item)
        return out
    raise ValueError(f"Unknown days value: {raw!r}")


def _parse_time(raw: str) -> dt.time:
    hh, mm = raw.split(":")
    return dt.time(hour=int(hh), minute=int(mm))


def parse_schedule(raw_schedule: list[dict]) -> list[Window]:
    out: list[Window] = []
    for i, item in enumerate(raw_schedule):
        try:
            name = item.get("name", f"window-{i}")
            days = _parse_days(item["days"])
            start = _parse_time(item["start"])
            end = _parse_time(item["end"])

            # New four-threshold format
            if "indoor_on_celsius" in item:
                indoor_on = float(item["indoor_on_celsius"])
                outdoor_on = float(item["outdoor_on_celsius"])
                indoor_off = float(item["indoor_off_celsius"])
                outdoor_off = float(item["outdoor_off_celsius"])
            elif "outdoor_threshold_celsius" in item:
                # Migration from old single-threshold format
                old_thresh = float(item["outdoor_threshold_celsius"])
                indoor_on = 18.0
                outdoor_on = old_thresh
                indoor_off = 20.0
                outdoor_off = old_thresh + 2.0
                log.warning(
                    "Schedule window %r uses deprecated 'outdoor_threshold_celsius'. "
                    "Migrated to indoor_on=%.1f, outdoor_on=%.1f, indoor_off=%.1f, outdoor_off=%.1f. "
                    "Update your config.yaml to suppress this warning.",
                    name, indoor_on, outdoor_on, indoor_off, outdoor_off,
                )
            else:
                raise KeyError("indoor_on_celsius (or legacy outdoor_threshold_celsius)")

            out.append(Window(
                name=name,
                days=days,
                start=start,
                end=end,
                indoor_on_celsius=indoor_on,
                outdoor_on_celsius=outdoor_on,
                indoor_off_celsius=indoor_off,
                outdoor_off_celsius=outdoor_off,
            ))
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid schedule window #{i}: {e}") from e
    return out


def active_window(windows: list[Window], now: Optional[dt.datetime] = None) -> Optional[Window]:
    if now is None:
        now = dt.datetime.now()
    for w in windows:
        if w.is_active(now):
            return w
    return None
