"""
Operational entry point for the video worker.

Safe mode only.

Live queue processing remains disabled until a
supported rendering provider is configured.
"""

import argparse
import json

from worker_service import DEFAULT_POLL_INTERVAL


def main():
    parser = argparse.ArgumentParser(
        description="Azure Text-to-Video worker"
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate worker module configuration",
    )

    args = parser.parse_args()

    if args.check:
        print(json.dumps({
            "service": "textvideo-worker",
            "status": "ready-for-provider-integration",
            "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
            "live_processing_enabled": False,
        }, indent=2))

        return 0

    parser.error(
        "Live processing is disabled. "
        "Run --check to validate the worker."
    )


if __name__ == "__main__":
    raise SystemExit(main())
