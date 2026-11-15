"""
Fal API configuration diagnostics.

This module never submits inference requests.

It does not log, print, or persist the Fal API key.
"""

import argparse
import importlib.metadata
import json
import os
import re


MODEL_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"
    r"(?:/[A-Za-z0-9_-]+)*$"
)


def fal_configuration_status():
    model_id = os.getenv("FAL_MODEL_ID", "").strip()
    key = os.getenv("FAL_KEY", "").strip()

    try:
        sdk_version = importlib.metadata.version(
            "fal-client"
        )
        sdk_installed = True
    except importlib.metadata.PackageNotFoundError:
        sdk_version = None
        sdk_installed = False

    model_valid = bool(
        MODEL_ID_PATTERN.fullmatch(model_id)
    )

    return {
        "provider": "fal",
        "sdk_installed": sdk_installed,
        "sdk_version": sdk_version,
        "api_key_configured": bool(key),
        "model_configured": bool(model_id),
        "model_id_valid": model_valid,
        "model_id": model_id if model_valid else None,
        "live_rendering_enabled": (
            os.getenv(
                "ENABLE_LIVE_VIDEO_RENDERING", ""
            ).lower() == "true"
        ),
        "worker_consumption_enabled": (
            os.getenv(
                "ENABLE_WORKER_CONSUMPTION", ""
            ).lower() == "true"
        ),
        "api_access_verified": False,
        "model_access_verified": False,
        "billable_request_submitted": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect Fal API setup safely"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        required=True,
    )
    parser.parse_args(argv)

    status = fal_configuration_status()

    print(json.dumps(status, indent=2))

    if not status["sdk_installed"]:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
