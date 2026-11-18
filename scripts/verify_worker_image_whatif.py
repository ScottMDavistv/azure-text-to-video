#!/usr/bin/env python3
"""Fail-closed full-payload ARM what-if verifier for the guarded dev worker.

Never prints the raw what-if response. The caller must keep it in a private
temporary file and delete it on exit. This is a narrow image-only policy.
"""
import json
import sys
from pathlib import Path

SUBSCRIPTION = "41fbffdc-1793-44f9-b70f-220ea8a9bc6c"
RESOURCE_ID = (f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-textvideo-dev"
               "/providers/Microsoft.App/jobs/job-textvideo-worker-dev")
OLD_IMAGE = ("acrtextvideo1794671698.azurecr.io/textvideo-worker:"
             "692bb1b7f7bd467f1d64e7288115424d21506c12")
NEW_IMAGE = ("acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:"
             "219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646")
PATH = "properties.template.containers[0].image"


class PreviewRejected(ValueError):
    pass


def verify(payload):
    if not isinstance(payload, dict) or set(payload) - {"changes", "status"}:
        raise PreviewRejected("unexpected ARM preview envelope")
    changes = payload.get("changes")
    if not isinstance(changes, list) or len(changes) != 1:
        raise PreviewRejected("exactly one worker change is required")
    change = changes[0]
    if not isinstance(change, dict):
        raise PreviewRejected("invalid ARM resource change")
    if change.get("changeType") != "Modify":
        raise PreviewRejected("create/delete/ignore/no-change is forbidden")
    if str(change.get("resourceId", "")).casefold() != RESOURCE_ID.casefold():
        raise PreviewRejected("change is not the exact reviewed worker")
    # Reject unknown top-level change metadata that can obscure a different
    # interpretation of the preview; Azure before/after payloads are not trusted.
    allowed = {"resourceId", "changeType", "delta", "before", "after", "deploymentId"}
    if set(change) - allowed:
        raise PreviewRejected("unrecognized change metadata")
    delta = change.get("delta")
    if not isinstance(delta, list) or len(delta) != 1:
        raise PreviewRejected("only a single explicit image delta is allowed")
    item = delta[0]
    if not isinstance(item, dict) or set(item) != {
        "path", "propertyChangeType", "before", "after"
    }:
        raise PreviewRejected("unrecognized property delta")
    path = item.get("path")
    if not isinstance(path, str) or path.removeprefix("$.").removeprefix(".") != PATH:
        raise PreviewRejected("unexpected property changed")
    if (item["propertyChangeType"] != "Modify"
            or item["before"] != OLD_IMAGE or item["after"] != NEW_IMAGE):
        raise PreviewRejected("worker image change differs from approved tag-to-digest")
    return True


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("BLOCK: one private preview JSON file required", file=sys.stderr)
        return 2
    try:
        file = Path(args[0])
        if not file.is_file() or file.stat().st_size > 2_000_000:
            raise PreviewRejected("missing or oversized ARM preview")
        verify(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError, TypeError):
        print("BLOCK: ARM what-if not proven image-only", file=sys.stderr)
        return 2
    print("PASS: exactly one approved worker image-only ARM modification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
