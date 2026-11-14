"""
Optional service-level authentication for the job API.

Default: authentication enforcement disabled.

When REQUIRE_JOB_API_AUTH=true, every /jobs request
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
    return os.getenv(
        "REQUIRE_JOB_API_AUTH", ""
    ).strip().lower() in {"1", "true", "yes"}


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

    return hmac.compare_digest(supplied, expected)


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
