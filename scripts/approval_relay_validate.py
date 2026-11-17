#!/usr/bin/env python3
"""Pure offline validation of DRAFT approval-relay handoffs.

No network, tokens, dispatch or approval decisions. Pending requests only.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

REPO = "ScottMDavistv/azure-text-to-video"
SHA = re.compile(r"[0-9a-f]{40}\Z")
HANDOFF = re.compile(r"[a-z0-9][a-z0-9_-]{7,79}\Z")
ALLOWED = {"security_review", "remediation"}
AGENTS = {"claude", "copilot"}


def canonical_payload(payload):
    """Serialize request fields only, excluding mutable approval/execution."""
    fields = {k: v for k, v in payload.items() if k not in {"approval", "execution"}}
    return json.dumps(fields, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(payload):
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def validate_pending(payload):
    if not isinstance(payload, dict):
        raise ValueError("handoff must be an object")
    expected = {"schema_version", "handoff_id", "kind", "repository",
                "pr_number", "base_ref", "head_sha", "target_agent",
                "scope", "instructions", "approval", "execution"}
    if set(payload) != expected:
        raise ValueError("unexpected or missing handoff fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported schema")
    if not isinstance(payload["handoff_id"], str) or not HANDOFF.fullmatch(payload["handoff_id"]):
        raise ValueError("invalid handoff ID")
    if payload["kind"] not in ALLOWED or payload["target_agent"] not in AGENTS:
        raise ValueError("kind/agent not allowed")
    if payload["repository"] != REPO or payload["base_ref"] != "main":
        raise ValueError("repository/base not allowed")
    if type(payload["pr_number"]) is not int or payload["pr_number"] < 1:
        raise ValueError("invalid PR number")
    if not isinstance(payload["head_sha"], str) or not SHA.fullmatch(payload["head_sha"]):
        raise ValueError("invalid exact head SHA")
    for key in ("scope", "instructions"):
        value = payload[key]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 4000:
            raise ValueError("invalid " + key)
    approval = payload["approval"]
    if not isinstance(approval, dict) or set(approval) != {
        "state", "approved_by_user_id", "approved_payload_sha256",
        "approved_at", "expires_at", "nonce"}:
        raise ValueError("invalid approval structure")
    if approval != {"state": "pending", "approved_by_user_id": None,
                    "approved_payload_sha256": None, "approved_at": None,
                    "expires_at": None, "nonce": None}:
        raise ValueError("only pending, unapproved drafts may be validated")
    if payload["execution"] != {"state": "disabled", "provider": "manual-claude-cloud"}:
        raise ValueError("execution must be disabled")
    if payload["kind"] == "security_review" and payload["target_agent"] != "claude":
        raise ValueError("security-review pilot must target independent Claude")
    return digest(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path, help="JSON draft to validate offline")
    args = parser.parse_args()
    payload = json.loads(args.handoff.read_text(encoding="utf-8"))
    print("PENDING DRAFT ONLY; NOT AUTHORIZED OR DISPATCHED")
    print("Request SHA-256:", validate_pending(payload))


if __name__ == "__main__":
    main()
