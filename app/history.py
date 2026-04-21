"""
History ring buffer + JSONL persistence.

One sample per tick (default 60s). Ring buffer holds up to MAX_POINTS entries
(1440 = 24 hours at 60s). Samples are also appended to a JSONL file that is
rotated when records older than RETENTION_DAYS are detected on append.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MAX_POINTS = 1440          # 24 hours at 60s per tick
RETENTION_DAYS = 7
HISTORY_FILE = Path("/var/lib/heating-brain/history.jsonl")


@dataclass
class HistorySample:
    ts: float              # unix timestamp
    indoor_temp_c: Optional[float]
    outdoor_temp_c: Optional[float]
    heating_on: bool       # True = heating commanded ON


class HistoryBuffer:
    def __init__(
        self,
        max_points: int = MAX_POINTS,
        history_file: Path = HISTORY_FILE,
    ) -> None:
        self._lock = threading.Lock()
        self._buf: deque[HistorySample] = deque(maxlen=max_points)
        self._file = history_file
        self._load_from_file()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_from_file(self) -> None:
        """Load recent history from JSONL file on startup."""
        if not self._file.exists():
            return
        cutoff = time.time() - RETENTION_DAYS * 86400
        loaded = 0
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("ts", 0) < cutoff:
                            continue
                        self._buf.append(HistorySample(
                            ts=float(d["ts"]),
                            indoor_temp_c=d.get("indoor_temp_c"),
                            outdoor_temp_c=d.get("outdoor_temp_c"),
                            heating_on=bool(d.get("heating_on", False)),
                        ))
                        loaded += 1
                    except (KeyError, ValueError, json.JSONDecodeError):
                        pass  # skip corrupt lines
        except OSError as e:
            log.warning("Could not read history file %s: %s", self._file, e)
        if loaded:
            log.info("Loaded %d history samples from %s", loaded, self._file)

    def _append_to_file(self, sample: HistorySample) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(sample)) + "\n")
        except OSError as e:
            log.warning("Could not write history sample: %s", e)

    def _rotate_file_if_needed(self) -> None:
        """Rewrite the file dropping records older than RETENTION_DAYS."""
        if not self._file.exists():
            return
        cutoff = time.time() - RETENTION_DAYS * 86400
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            fresh = [
                l for l in lines
                if l.strip() and _ts_from_line(l) >= cutoff
            ]
            if len(fresh) < len(lines):
                tmp = self._file.with_suffix(".tmp")
                tmp.write_text("".join(fresh), encoding="utf-8")
                tmp.replace(self._file)
                log.debug(
                    "History rotated: dropped %d old records", len(lines) - len(fresh)
                )
        except OSError as e:
            log.warning("History rotation failed: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def add(self, sample: HistorySample) -> None:
        with self._lock:
            self._buf.append(sample)
        self._append_to_file(sample)
        # Rotate at most once per hour (every 60 samples at default 60s tick).
        if len(self._buf) % 60 == 0:
            self._rotate_file_if_needed()

    def get(self, hours: float = 24.0) -> list[dict]:
        cutoff = time.time() - hours * 3600
        with self._lock:
            return [
                asdict(s) for s in self._buf
                if s.ts >= cutoff
            ]


def _ts_from_line(line: str) -> float:
    try:
        return float(json.loads(line).get("ts", 0))
    except (ValueError, json.JSONDecodeError):
        return 0.0
