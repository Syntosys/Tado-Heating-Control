"""
Schedule engine.

A schedule is a list of windows, each with:
  - days: "all" | "weekdays" | "weekends" | list of ["mon","tue",...]
  - start, end: "HH:MM" local time
  - outdoor_threshold_celsius: float — if outdoor temp is below this while the
    window is active, the controller may turn heat on.

Outside every window, heating is forced off.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = {"mon", "tue", "wed", "thu", "fri"}
WEEKENDS = {"sat", "sun"}


@dataclass
class Window:
    name: str
    days: set[str]  # subset of DAY_NAMES
    start: dt.time
    end: dt.time
    outdoor_threshold_celsius: float

    def is_active(self, now: dt.datetime) -> bool:
        day_name = DAY_NAMES[now.weekday()]
        if day_name not in self.days:
            return False
        t = now.time()
        if self.start <= self.end:
            return self.start <= t <= self.end
        # Wraps midnight (e.g. 22:00-06:00)
        return t >= self.start or t <= self.end


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
            out.append(Window(
                name=item.get("name", f"window-{i}"),
                days=_parse_days(item["days"]),
                start=_parse_time(item["start"]),
                end=_parse_time(item["end"]),
                outdoor_threshold_celsius=float(item["outdoor_threshold_celsius"]),
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
