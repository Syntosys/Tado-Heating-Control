"""
Weather source for the heating brain.

Uses Open-Meteo (https://open-meteo.com) — free, no API key, no rate limit for personal use.
One of the providers supported by the MM² weather module, so "same source MM² uses"
holds as long as your MM² config uses open-meteo (or you can swap).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class WeatherReading:
    temperature_celsius: float
    fetched_at: float  # unix timestamp

    def age_seconds(self) -> float:
        return time.time() - self.fetched_at


class WeatherProvider:
    def __init__(self, latitude: float, longitude: float):
        self.latitude = latitude
        self.longitude = longitude
        self._cache: Optional[WeatherReading] = None

    def fetch(self) -> WeatherReading:
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": "temperature_2m",
            "timezone": "auto",
        }
        r = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        temp = float(data["current"]["temperature_2m"])
        reading = WeatherReading(temperature_celsius=temp, fetched_at=time.time())
        self._cache = reading
        log.debug("Weather: %.1f°C", temp)
        return reading

    def cached(self) -> Optional[WeatherReading]:
        return self._cache
