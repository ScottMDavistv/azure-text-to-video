"""
Safe provider configuration inspection.

Outputs status information only.
Never prints API keys.
"""

import argparse
import json

from provider_status import check_runtime_environment


def main():
    parser = argparse.ArgumentParser(
        description="Inspect video-provider configuration"
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Print secret-free provider status",
    )

    args = parser.parse_args()

    if not args.check:
        parser.error("Use --check")

    print(json.dumps(
        check_runtime_environment(),
        indent=2,
    ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
