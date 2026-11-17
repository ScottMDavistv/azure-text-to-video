"""
Administrative job-management authentication.

Disabled by default.

This is a temporary service-level control, not per-user
authentication or ownership authorization.
"""

import hmac
import os

from fastapi import HTTPException


MIN_TOKEN_LENGTH = 32


def _ascii_token(value):
    if not isinstance(value, str):
        return ""
    token = value.strip()
    if not token or any(ch.isspace() for ch in token):
        return ""
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        return ""
    return token


def _valid_job_admin_token(value):
    token = _ascii_token(value)
    return len(token) >= MIN_TOKEN_LENGTH


def admin_endpoints_enabled():
    return os.getenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS", ""
    ).strip().lower() in {"true", "1", "yes"}


def authorize_job_admin(authorization=None):
    if not admin_endpoints_enabled():
        raise HTTPException(
            status_code=503,
            detail="Job administration is disabled",
        )

    expected = os.getenv(
        "JOB_ADMIN_TOKEN", ""
    ).strip()

    if not _valid_job_admin_token(expected):
        raise HTTPException(
            status_code=503,
            detail="Job administration is not configured",
        )

    provided = ""

    if isinstance(authorization, str):
        scheme, separator, token = (
            authorization.partition(" ")
        )

        if separator and scheme.lower() == "bearer":
            provided = _ascii_token(token)

    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True
