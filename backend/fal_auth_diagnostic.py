"""
Read-only Fal API connectivity/authentication diagnostic.

Makes exactly one GET request to a fabricated queue request ID.

Never:
- submits a generation
- polls a real request
- downloads an asset
- prints an API key
- prints response bodies
- prints authorization headers

A 404 indicates endpoint reachability, not guaranteed
authentication or model entitlement.
"""

import json
import os
import re
import uuid

import requests


MODEL_PATTERN = re.compile(
    r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+"
    r"(?:/[a-zA-Z0-9_-]+)*$"
)

DEFAULT_TIMEOUT = (5, 15)


def check_fal_api(*, get=None, key=None, model_id=None):
    if get is None:
        get = requests.get

    if key is None:
        key = os.environ.get("FAL_KEY", "")

    if model_id is None:
        model_id = os.environ.get("FAL_MODEL_ID", "")

    key = key.strip()
    model_id = model_id.strip()

    base = {
        "operation": "read_only_queue_status",
        "request_submitted": False,
        "generation_started": False,
        "billable_generation_requested": False,
        "model_access_verified": False,
        "authentication_verified": False,
    }

    if not key:
        return {
            **base,
            "outcome": "missing_credential",
            "http_status": None,
        }

    if not MODEL_PATTERN.fullmatch(model_id):
        return {
            **base,
            "outcome": "invalid_model_identifier",
            "http_status": None,
        }

    # Fal's Queue Status API uses owner/app, not the
    # full model endpoint path.
    owner, app = model_id.split("/")[:2]

    # A new unpredictable ID prevents accidentally
    # querying a real request.
    request_id = str(uuid.uuid4())

    url = (
        f"https://queue.fal.run/{owner}/{app}"
        f"/requests/{request_id}/status"
    )

    try:
        response = get(
            url,
            headers={
                "Authorization": f"Key {key}",
                "Accept": "application/json",
            },
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        # Deliberately suppress exception text, which
        # may contain operational details.
        return {
            **base,
            "outcome": "network_error",
            "http_status": None,
        }

    status = response.status_code

    if status == 404:
        outcome = "reachable_auth_inconclusive"
    elif status == 401:
        outcome = "authentication_rejected"
    elif status == 403:
        outcome = "access_denied"
    elif 300 <= status < 400:
        outcome = "unexpected_redirect"
    elif status in (200, 202):
        outcome = "unexpected_existing_request"
    elif status == 429:
        outcome = "rate_limited"
    elif 500 <= status < 600:
        outcome = "provider_server_error"
    else:
        outcome = "unexpected_http_status"

    return {
        **base,
        "outcome": outcome,
        "http_status": status,
    }


def diagnostic_exit_code(result):
    outcome = result["outcome"]

    if outcome == "reachable_auth_inconclusive":
        return 0

    if outcome in (
        "missing_credential",
        "invalid_model_identifier",
    ):
        return 2

    if outcome in (
        "authentication_rejected",
        "access_denied",
    ):
        return 3

    return 4


def main():
    result = check_fal_api()

    print(json.dumps(result, indent=2))

    return diagnostic_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
