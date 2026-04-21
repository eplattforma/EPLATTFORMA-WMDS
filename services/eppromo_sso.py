"""eppromo SSO helper.

Generates a signed one-time SSO URL for the eppromo app using a shared
HMAC-SHA256 secret. The token format is:

    token = base64url(payload) + "." + base64url(signature)
    payload   = ASCII string of current Unix timestamp (seconds)
    signature = HMAC-SHA256(EPPROMO_SSO_SECRET, payload) -- raw 32 bytes

Both halves are URL-safe base64 with trailing "=" stripped. The token is
valid for +/- 300 seconds of eppromo server time.

Configuration is read from environment variables:
    EPPROMO_SSO_SECRET  -- shared secret (must match eppromo)
    EPPROMO_BASE_URL    -- base URL of eppromo (e.g. https://eppromo.replit.app)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from base64 import urlsafe_b64encode

logger = logging.getLogger(__name__)


class EppromoSSOConfigError(Exception):
    """Raised when EPPROMO_SSO_SECRET or EPPROMO_BASE_URL is missing."""


def _b64url(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _read_config() -> tuple[str, str]:
    secret = (os.environ.get("EPPROMO_SSO_SECRET") or "").strip()
    base_url = (os.environ.get("EPPROMO_BASE_URL") or "").strip().rstrip("/")
    if not secret:
        raise EppromoSSOConfigError("EPPROMO_SSO_SECRET is not set")
    if not base_url:
        raise EppromoSSOConfigError("EPPROMO_BASE_URL is not set")
    return secret, base_url


def is_configured() -> bool:
    """Return True if both required env vars are present."""
    try:
        _read_config()
        return True
    except EppromoSSOConfigError:
        return False


def generate_token(timestamp: int | None = None) -> str:
    """Generate the signed eppromo SSO token (payload.signature)."""
    secret, _ = _read_config()
    ts = int(timestamp if timestamp is not None else time.time())
    payload = str(ts).encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def build_login_url(timestamp: int | None = None) -> str:
    """Return the full eppromo /sso login URL with a fresh signed token."""
    _, base_url = _read_config()
    token = generate_token(timestamp=timestamp)
    return f"{base_url}/sso?token={token}"
