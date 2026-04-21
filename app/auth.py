"""
PIN cookie authentication helpers.

A 4-digit PIN is configured in config.yaml under http.pin.

On successful PIN entry the server sets a signed cookie using HMAC-SHA256.
The server secret is auto-generated and persisted to
/var/lib/heating-brain/cookie_secret (mode 0600) on first run.

Cookie format:  <timestamp>.<hmac_hex>
The timestamp is the unix second the cookie was issued. Cookies are valid
for COOKIE_MAX_AGE_DAYS days.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COOKIE_NAME = "hb_session"
COOKIE_MAX_AGE_DAYS = 30
SECRET_FILE = Path("/var/lib/heating-brain/cookie_secret")


def _load_or_create_secret(secret_file: Path = SECRET_FILE) -> bytes:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        try:
            data = secret_file.read_bytes().strip()
            if len(data) >= 32:
                return data
            log.warning("Cookie secret file too short — regenerating.")
        except OSError as e:
            log.warning("Could not read cookie secret: %s — regenerating.", e)
    # Generate a new 64-byte hex secret.
    new_secret = secrets.token_hex(32).encode()
    tmp = secret_file.with_suffix(".tmp")
    tmp.write_bytes(new_secret)
    os.chmod(tmp, 0o600)
    tmp.replace(secret_file)
    os.chmod(secret_file, 0o600)
    log.info("Generated new cookie secret at %s", secret_file)
    return new_secret


# Module-level secret — loaded once at import time so all requests share it.
_secret: Optional[bytes] = None


def _get_secret() -> bytes:
    global _secret
    if _secret is None:
        _secret = _load_or_create_secret()
    return _secret


def _sign(payload: str, secret: bytes) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def make_cookie_value() -> str:
    """Return a signed cookie value: '<ts>.<hmac>'."""
    ts = str(int(time.time()))
    sig = _sign(ts, _get_secret())
    return f"{ts}.{sig}"


def verify_cookie_value(value: str) -> bool:
    """Return True if the cookie is valid and not expired."""
    try:
        ts_str, sig = value.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    # Check expiry.
    max_age = COOKIE_MAX_AGE_DAYS * 86400
    if time.time() - ts > max_age:
        return False
    # Constant-time comparison.
    expected = _sign(ts_str, _get_secret())
    return hmac.compare_digest(expected, sig)


def check_pin(provided: str, configured: str) -> bool:
    """Constant-time PIN comparison."""
    if not provided or not configured:
        return False
    return hmac.compare_digest(str(provided), str(configured))
