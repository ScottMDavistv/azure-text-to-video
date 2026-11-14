"""
Local, secret-free operational configuration inspector.

Does not connect to Azure or Fal.
"""

import argparse
import json

from runtime_security import (
    get_runtime_security_status,
)


def main():
    parser = argparse.ArgumentParser(
        description="Video Render backend operational checks"
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Print safe operational configuration",
    )

    args = parser.parse_args()

    if not args.check:
        parser.error("Use --check")

    print(
        json.dumps(
            {
                "service": "textvideo-backend",
                "security": get_runtime_security_status(),
                "live_worker_started": False,
                "azure_connectivity_checked": False,
                "provider_connectivity_checked": False,
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
