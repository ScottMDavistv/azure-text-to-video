"""
Administrative job-management authentication.

Disabled by default.

This is a temporary service-level control, not per-user
authentication or ownership authorization.
"""

import hmac
import os
import re

from fastapi import HTTPException


MIN_TOKEN_LENGTH = 32

# RFC 7235 token68, ASCII only. A configured secret outside this grammar
# cannot be supplied reliably in an HTTP Authorization header (ASGI decodes
# header bytes as latin-1), so it is treated as a configuration error.
_TOKEN68 = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


def _token68_bytes(value):
    """Return ASCII bytes for a valid token68 string, otherwise None.

    Never raises: lone surrogates, other non-ASCII code points and
    non-string values are rejected rather than encoded.
    """
    if not isinstance(value, str) or not value.isascii():
        return None
    if _TOKEN68.fullmatch(value) is None:
        return None
    return value.encode("ascii")


def admin_endpoints_enabled():
    return os.getenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS", ""
    ).strip().lower() in {"true", "1", "yes"}


def _configured_token_bytes():
    expected = _token68_bytes(
        os.getenv("JOB_ADMIN_TOKEN", "").strip()
    )
    if expected is None or len(expected) < MIN_TOKEN_LENGTH:
        return None
    return expected


def job_admin_token_configured():
    """Secret-free configuration check; never returns the token."""
    return _configured_token_bytes() is not None


def authorize_job_admin(authorization=None):
    if not admin_endpoints_enabled():
        raise HTTPException(
            status_code=503,
            detail="Job administration is disabled",
        )

    expected = _configured_token_bytes()

    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Job administration is not configured",
        )

    provided = None

    if isinstance(authorization, str):
        scheme, separator, token = (
            authorization.partition(" ")
        )

        if separator and scheme.lower() == "bearer":
            provided = _token68_bytes(token.strip())

    if provided is None or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True
