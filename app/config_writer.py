"""
Safe rewrite of the schedule: section of config.yaml.

Strategy:
  - Read the existing config file line by line.
  - Strip the old schedule: block (from "schedule:" to the next top-level key
    or end of file).
  - Append a freshly-serialised schedule: block.
  - Write atomically via a temp file.
  - Use a lock file to prevent concurrent writes from multiple requests.

Comments in other sections are preserved because we only touch the schedule block.
Comments inside the existing schedule block are lost (acceptable per spec).
"""

from __future__ import annotations

import logging
import sys

try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    # fcntl is Unix-only; on Windows (dev/test machines) we fall back to a
    # no-op lock so imports succeed. Production runs on Linux (Raspberry Pi).
    _HAS_FCNTL = False
    _fcntl = None  # type: ignore
import os
import re
import time
from pathlib import Path
from typing import Optional

import yaml

from .schedule import Window

log = logging.getLogger(__name__)

LOCK_FILE = Path("/var/lib/heating-brain/config.lock")
LOCK_TIMEOUT = 10  # seconds


def _serialise_window(w: Window) -> dict:
    return w.to_dict()


def _windows_to_yaml(windows: list[Window]) -> str:
    """Produce a clean YAML block for the schedule section."""
    items = [_serialise_window(w) for w in windows]
    return yaml.dump({"schedule": items}, default_flow_style=False, allow_unicode=True)


def _strip_schedule_block(lines: list[str]) -> list[str]:
    """
    Remove the existing schedule: block from the file lines.
    Returns the remaining lines (other sections intact).
    """
    out: list[str] = []
    in_schedule = False
    for line in lines:
        # Detect start of schedule block (top-level key)
        if re.match(r"^schedule\s*:", line):
            in_schedule = True
            continue
        if in_schedule:
            # A new top-level key (not indented, not blank, not a comment) ends the block.
            if line and not line[0].isspace() and not line.startswith("#"):
                in_schedule = False
                out.append(line)
            # else: skip (part of old schedule block)
        else:
            out.append(line)
    return out


def _lock_acquire(fd) -> None:
    """Acquire an exclusive file lock (Unix only; no-op on Windows)."""
    if not _HAS_FCNTL:
        return
    deadline = time.time() + LOCK_TIMEOUT
    while True:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.time() > deadline:
                raise OSError("Timed out waiting for config lock")
            time.sleep(0.1)


def _lock_release(fd) -> None:
    """Release file lock (Unix only; no-op on Windows)."""
    if not _HAS_FCNTL:
        return
    _fcntl.flock(fd, _fcntl.LOCK_UN)


def write_schedule(config_path: Path, windows: list[Window]) -> None:
    """
    Replace the schedule: section of config_path with the given windows.
    Thread-safe via a lock file on Linux. Raises OSError on failure.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        _lock_acquire(lock_fd)

        config_path = Path(config_path)
        original = config_path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)

        remaining = _strip_schedule_block(lines)

        # Ensure the file ends with a newline before appending the new block.
        body = "".join(remaining)
        if body and not body.endswith("\n"):
            body += "\n"

        new_schedule_yaml = _windows_to_yaml(windows)
        new_content = body + new_schedule_yaml

        tmp = config_path.with_suffix(".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(config_path)
        log.info("Config schedule section updated (%d windows).", len(windows))

    finally:
        _lock_release(lock_fd)
        lock_fd.close()


def _strip_key_from_section(lines: list[str], section: str, key: str) -> list[str]:
    """
    Remove a specific key line from within a named section block.
    Only removes the single matching key line (simple scalar value).
    """
    out: list[str] = []
    in_section = False
    key_pattern = re.compile(r"^(\s+)" + re.escape(key) + r"\s*:")
    for line in lines:
        if re.match(r"^" + re.escape(section) + r"\s*:", line):
            in_section = True
            out.append(line)
            continue
        if in_section:
            if line and not line[0].isspace() and not line.startswith("#"):
                in_section = False
                out.append(line)
                continue
            if key_pattern.match(line):
                continue  # skip the old key line
        out.append(line)
    return out


def patch_config(config_path: Path, section: str, key: str, value: str) -> None:
    """
    Set a scalar key within an existing YAML section to a new value.

    Rewrites the matching ``key: <value>`` line inside ``section:`` in-place,
    preserving all comments and other content.  If the key does not exist it is
    appended to the section.  Uses the same lock-and-atomic-replace strategy as
    ``write_schedule``.

    Args:
        config_path: Path to config.yaml.
        section: Top-level YAML section name (e.g. "http").
        key: Key within that section (e.g. "pin").
        value: New string value to write (will be single-quoted in YAML).
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        _lock_acquire(lock_fd)

        config_path = Path(config_path)
        original = config_path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)

        # Find the indentation of the section and insert/replace the key.
        new_lines: list[str] = []
        in_section = False
        key_written = False
        section_indent = "  "  # default; will be detected from existing keys
        key_pattern = re.compile(r"^(\s+)" + re.escape(key) + r"\s*:")

        for i, line in enumerate(lines):
            if re.match(r"^" + re.escape(section) + r"\s*:", line):
                in_section = True
                key_written = False
                new_lines.append(line)
                continue

            if in_section:
                # Detect indentation from first indented line in this section
                if line and line[0].isspace():
                    m = re.match(r"^(\s+)", line)
                    if m:
                        section_indent = m.group(1)

                # End of section — write key if not yet written
                if line and not line[0].isspace() and not line.startswith("#"):
                    if not key_written:
                        new_lines.append(f"{section_indent}{key}: '{value}'\n")
                        key_written = True
                    in_section = False
                    new_lines.append(line)
                    continue

                # Replace existing key line
                if key_pattern.match(line):
                    new_lines.append(f"{section_indent}{key}: '{value}'\n")
                    key_written = True
                    continue

            new_lines.append(line)

        # If section ended at EOF without writing the key
        if in_section and not key_written:
            new_lines.append(f"{section_indent}{key}: '{value}'\n")

        new_content = "".join(new_lines)
        tmp = config_path.with_suffix(".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(config_path)
        log.info("Config %s.%s updated.", section, key)

    finally:
        _lock_release(lock_fd)
        lock_fd.close()


def write_http_pin(config_path: Path, new_pin: str) -> None:
    """
    Rewrite the ``http.pin`` field in config_path.
    The value is stored as a plain 4-digit string.
    Raises ValueError for invalid pins, OSError on write failure.
    """
    if not re.match(r"^\d{4}$", str(new_pin)):
        raise ValueError("PIN must be exactly 4 digits.")
    patch_config(config_path, "http", "pin", str(new_pin))
