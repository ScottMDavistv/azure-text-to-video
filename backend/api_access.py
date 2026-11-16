"""
Fail-closed service-level authentication for the legacy job API.

Default: authentication enforcement enabled. Only an explicit
ALLOW_UNAUTHENTICATED_DEV=true override permits unsafe local development.

Unless this deliberate development override is enabled, every /jobs request
requires an API_ACCESS_TOKEN Bearer token.

This is a temporary service credential, not per-user
authentication or ownership authorization.
"""

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


MINIMUM_TOKEN_LENGTH = 32


def api_auth_required():
    # A missing or false REQUIRE_JOB_API_AUTH is not sufficient to turn
    # off authentication. Only this explicit local-dev-only opt-out can.
    return os.getenv(
        "ALLOW_UNAUTHENTICATED_DEV", ""
    ).strip().lower() not in {"1", "true", "yes"}


def protected_job_path(path):
    return path == "/jobs" or path.startswith("/jobs/")


def api_auth_configured():
    return len(
        os.getenv("API_ACCESS_TOKEN", "").strip()
    ) >= MINIMUM_TOKEN_LENGTH


def check_api_authorization(authorization):
    expected = os.getenv(
        "API_ACCESS_TOKEN", ""
    ).strip()

    if len(expected) < MINIMUM_TOKEN_LENGTH:
        return False

    supplied = ""

    if isinstance(authorization, str):
        scheme, separator, token = (
            authorization.partition(" ")
        )

        if separator and scheme.lower() == "bearer":
            supplied = token.strip()

    return hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8'))


class JobApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not protected_job_path(request.url.path):
            return await call_next(request)

        if not api_auth_required():
            return await call_next(request)

        if not api_auth_configured():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Job API authentication "
                        "is not configured"
                    )
                },
                headers={"Cache-Control": "no-store"},
            )

        if not check_api_authorization(
            request.headers.get("authorization")
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Unauthorized"
                },
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Cache-Control": "no-store",
                },
            )

        return await call_next(request)
