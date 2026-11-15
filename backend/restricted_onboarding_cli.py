"""Manual, non-networked-by-default interface for administrator onboarding.

Use an interactive, trusted operator workstation with delegated API access
tokens and separately approved Azure Table Data Contributor credentials.
Never put tokens on argv, in environment variables or GitHub Actions.
"""
import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path

from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential

from restricted_onboarding import (
    propose_enrollment, approve_enrollment, execute_enrollment,
)
from project_models import validate_id

TENANT = "773e9051-c546-4b75-9432-90f4f8242660"
APP_ID = "a5a09b74-aead-455f-9de7-9b844c8a8465"
ACCOUNT = "sttextvideo1794671698"
TABLE = "VideoJobs"


def load_admin_allowlist(path):
    file_path = Path(path).expanduser()
    if file_path.is_symlink() or not file_path.is_file():
        raise ValueError("A regular, non-symlink administrator policy file is required")
    mode = file_path.stat().st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("Administrator policy file must not be group/world-writable")
    with file_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if (not isinstance(data, dict) or
            data.get("tenantId") != TENANT or data.get("apiClientId") != APP_ID or
            data.get("storageAccount") != ACCOUNT or data.get("tableName") != TABLE or
            not isinstance(data.get("adminDigests"), list) or
            len(data["adminDigests"]) < 2):
        raise ValueError("Trusted policy requires correct tenant, API, storage and two administrators")
    # Only a restricted administrator provisions this file; never accept
    # a user-submitted API request as the policy source.
    return frozenset(data["adminDigests"])


def main(argv=None, *, input_token=None, table_factory=None):
    parser = argparse.ArgumentParser(
        description="Restricted first-owner enrollment; operator-only")
    parser.add_argument("action", choices=("propose", "approve", "execute"))
    parser.add_argument("--admin-policy", required=True,
                        help="Local privileged, independently reviewed policy JSON")
    parser.add_argument("--enrollment-id", required=True)
    parser.add_argument("--project-name", help="Required for propose only")
    parser.add_argument("--execute-writes", action="store_true",
                        help="Explicit acknowledgement of Azure Table writes")
    args = parser.parse_args(argv)
    validate_id(args.enrollment_id)
    if not args.execute_writes:
        parser.error("Refusing Azure writes without --execute-writes")
    if args.action == "propose" and not args.project_name:
        parser.error("--project-name required to propose")
    allowed = load_admin_allowlist(args.admin_policy)
    os.environ["PROJECT_OIDC_ISSUER"] = f"https://login.microsoftonline.com/{TENANT}/v2.0"
    os.environ["PROJECT_OIDC_JWKS_URL"] = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
    os.environ["PROJECT_OIDC_AUDIENCE"] = f"api://{APP_ID}"

    get_token = input_token or getpass.getpass
    # Keep raw access tokens out of argv, logs, environment variables
    # and persisted audit entities.
    operator_token = get_token("Administrator API access token (hidden input): ")
    target_token = None
    if args.action in ("propose", "execute"):
        target_token = get_token("Target user's API access token (hidden input): ")

    if table_factory is None:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        table = TableServiceClient(
            endpoint=f"https://{ACCOUNT}.table.core.windows.net",
            credential=credential,
        ).get_table_client(TABLE)
    else:
        table = table_factory()

    if args.action == "propose":
        result = propose_enrollment(
            table, enrollment_id=args.enrollment_id, project_name=args.project_name,
            proposer_token=operator_token, target_token=target_token,
            allowed_admin_digests=allowed,
        )
    elif args.action == "approve":
        result = approve_enrollment(
            table, enrollment_id=args.enrollment_id, approver_token=operator_token,
            allowed_admin_digests=allowed,
        )
    else:
        result = execute_enrollment(
            table, enrollment_id=args.enrollment_id, operator_token=operator_token,
            target_token=target_token, allowed_admin_digests=allowed,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Enrollment blocked: {type(exc).__name__}; check trusted approval and Azure permissions.",
              file=sys.stderr)
        sys.exit(1)
