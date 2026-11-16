#!/usr/bin/env python3
"""Read-only ACR worker manifest evidence, separately gated from ARM preflight.

Uses only the existing OIDC Reader identity. Never grants registry permissions,
retrieves passwords, obtains tokens as output, or modifies images/resources.
An unavailable manifest is BLOCKED/UNVERIFIED, not a guessed digest.
"""
import json
import os
from pathlib import Path
import re
import subprocess

REGISTRY = "acrtextvideo1794671698"
REPOSITORY = "textvideo-worker"
EXPECTED_TAG = "692bb1b7f7bd467f1d64e7288115424d21506c12"
IMAGE = f"{REGISTRY}.azurecr.io/{REPOSITORY}"
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
OUTPUT_JSON = Path("azure-acr-manifest-evidence.json")
OUTPUT_MD = Path("azure-acr-manifest-evidence.md")


def manifest_metadata(*, runner=subprocess.run):
    # Exact argv. Avoid all login credentials, acr login, import, update, delete,
    # token/credential output, repository list, or role writes.
    cmd = ["az", "acr", "manifest", "show-metadata",
           "--registry", REGISTRY,
           "--name", REPOSITORY + ":" + EXPECTED_TAG,
           "--output", "json"]
    try:
        result = runner(cmd, capture_output=True, text=True,
                        check=False, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None  # Never serialize raw stderr / inaccessible repository details
    try:
        metadata = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    digest = metadata.get("digest")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        return None
    return digest


def report_for_digest(digest):
    verified = isinstance(digest, str) and bool(DIGEST_RE.fullmatch(digest))
    # This is an authenticated metadata lookup, not signature, scan, pull or provenance.
    return {
        "schema_version": 1,
        "check": "read-only ACR manifest metadata lookup",
        "state": "verified-metadata" if verified else "blocked-unverified",
        "registry": REGISTRY,
        "repository": REPOSITORY,
        "source_tag": EXPECTED_TAG,
        "manifest_digest": digest if verified else None,
        "candidate_immutable_image": IMAGE + "@" + digest if verified else None,
        "no_azure_changes": True,
        "not_verified": [
            "Independent manifest integrity/provenance and signature",
            "Container image pull entitlement, execution, and runtime safety",
            "Image registry retention and rollback availability",
            "Approval to merge or deploy or alter Azure IAM",
        ] if verified else [
            "ACR registry data-plane read entitlement or manifest availability",
            "Actual OCI manifest digest (no fallback to Git SHA tag)",
            "Independent provenance, pull, deployment and runtime readiness",
        ],
    }


def write_report(report):
    OUTPUT_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Azure ACR manifest digest evidence",
        "",
        f"**State:** {report['state']}",
        f"**Registry:** {REGISTRY}",
        f"**Repository:** {REPOSITORY}",
        f"**Git-SHA source tag:** \`{EXPECTED_TAG}\`",
        "**Manifest digest:** " + (f"\`{report['manifest_digest']}\`"
                                   if report["manifest_digest"] else "NOT VERIFIED"),
        "**Candidate immutable image:** " + (f"\`{report['candidate_immutable_image']}\`"
                                            if report["candidate_immutable_image"] else "NOT VERIFIED"),
        "",
        "Metadata verification never authorizes deployment, registry writes, or IAM grants.",
        "",
        "### Not verified",
    ]
    lines.extend("- " + item for item in report["not_verified"])
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise SystemExit("This audit must only execute on protected main")
    if os.environ.get("AZURE_AUDIT_OPERATIONS_ENABLED") != "true":
        raise SystemExit("Dedicated Azure audit approval variable is not enabled")
    try:
        digest = manifest_metadata()
    except Exception:
        digest = None  # Do not expose raw registry errors, fail evidence closed
    report = report_for_digest(digest)
    write_report(report)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as stream:
            stream.write(OUTPUT_MD.read_text(encoding="utf-8"))
    print("Read-only ACR manifest evidence:", report["state"])
    # A blocked/unverified check should not make the whole ARM audit appear broken;
    # its separate artifact and summary must clearly indicate it is NOT verified.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
