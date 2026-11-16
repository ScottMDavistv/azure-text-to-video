"""Local/CI fail-closed Stage A configuration check.

This CLI is NOT a scheduler or deployment tool.
"""

import argparse
import json
from stage_a_runtime import validate_safe_defaults


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args(argv)
    print(json.dumps(validate_safe_defaults(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
