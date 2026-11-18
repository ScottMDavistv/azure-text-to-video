#!/usr/bin/env python3
"""Fail-closed worker ACR digest validation and optional read-only digest lookup.

No Azure writes, image publishing, deployment, job start or paid services.
A digest is not trusted merely because it is syntactically valid: use resolve
against ACR and retain its source-tag and manifest evidence.
"""
import argparse
import json
import re
import subprocess
import sys

REGISTRY = "acrtextvideo1794671698"
REPOSITORY = "textvideo-worker"
LOGIN = REGISTRY + ".azurecr.io"
TAG_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE_PATTERN = re.compile(
    re.escape(LOGIN + "/" + REPOSITORY + "@") + r"sha256:[0-9a-f]{64}\Z"
)


def validate_image(image: str) -> str:
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError(
            "Expected exact registry/repository and immutable @sha256 digest "
            "(mutable tags and malformed values are forbidden)"
        )
    return image


def validate_digest(digest: str) -> str:
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("ACR did not return a canonical sha256 manifest digest")
    return digest


def resolve_digest(commit_tag: str, runner=subprocess.run) -> str:
    if not TAG_PATTERN.fullmatch(commit_tag):
        raise ValueError("Expected full lowercase 40-character Git commit tag")
    # Restrict Azure CLI to exactly one read-only manifest metadata operation.
    result = runner(
        ["az", "acr", "manifest", "show-metadata",
         "--registry", REGISTRY, "--name", REPOSITORY + ":" + commit_tag,
         "--output", "json"],
        capture_output=True, text=True, check=False, timeout=45,
    )
    if result.returncode:
        raise RuntimeError(
            "ACR manifest metadata unavailable with current identity; "
            "verify registry data-plane read permissions, no fallback to tag"
        )
    try:
        metadata = json.loads(result.stdout)
        digest = validate_digest(metadata.get("digest"))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("ACR returned missing/invalid manifest digest") from exc
    return validate_image(f"{LOGIN}/{REPOSITORY}@{digest}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate", metavar="IMAGE", help="Offline digest syntax validation")
    modes.add_argument("--resolve-tag", metavar="COMMIT_SHA", help="Read-only ACR lookup")
    args = parser.parse_args(argv)
    try:
        value = validate_image(args.validate) if args.validate is not None else resolve_digest(args.resolve_tag)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Worker image digest gate BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
