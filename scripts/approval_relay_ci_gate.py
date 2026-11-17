#!/usr/bin/env python3
"""Offline, fail-closed CI gate for coding-agent remediation branches.

Consumes a trusted GitHub snapshot JSON prepared outside untrusted PR code.
The supplied immutable provenance must be independently verified by a trusted
adapter (protected owner-approved assignment record). A PR comment, branch
name or agent-produced file is NOT evidence of origin.
Exit 1 on any unknown, malformed or noncompliant input. No GitHub writes.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = "ScottMDavistv/azure-text-to-video"


def verify(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("invalid snapshot")
    expected = {"repository", "coding_pr", "target_pr", "head_branch",
                "base_branch", "target_branch", "target_head_sha",
                "origin_sha", "origin_verified", "merge_base_sha",
                "is_target_ancestor", "coding_head_sha", "draft",
                "source", "task_approved"}
    if set(snapshot) != expected:
        raise ValueError("missing/unexpected fields")
    if snapshot["repository"] != REPO:
        raise ValueError("wrong repository")
    if (type(snapshot["coding_pr"]) is not int or snapshot["coding_pr"] < 1 or
            type(snapshot["target_pr"]) is not int or snapshot["target_pr"] < 1 or
            snapshot["coding_pr"] == snapshot["target_pr"]):
        raise ValueError("invalid PR IDs")
    for field in ("target_head_sha", "origin_sha", "merge_base_sha", "coding_head_sha"):
        if not isinstance(snapshot[field], str) or not SHA.fullmatch(snapshot[field]):
            raise ValueError("invalid " + field)
    if (not snapshot["head_branch"] or not isinstance(snapshot["head_branch"], str)
            or not snapshot["target_branch"] or not isinstance(snapshot["target_branch"], str)
            or snapshot["target_branch"] == "main"):
        raise ValueError("invalid branch")
    if snapshot["base_branch"] != snapshot["target_branch"]:
        raise ValueError("wrong base")
    if snapshot["origin_verified"] is not True or snapshot["source"] != "trusted-protected-task-ledger":
        raise ValueError("branch creation provenance unverified")
    if snapshot["task_approved"] is not True or snapshot["draft"] is not True:
        raise ValueError("unapproved or nondraft remediation")
    if snapshot["origin_sha"] != snapshot["target_head_sha"]:
        raise ValueError("not created at target exact head")
    if snapshot["merge_base_sha"] != snapshot["target_head_sha"] or snapshot["is_target_ancestor"] is not True:
        raise ValueError("coding history does not contain target exact head")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path)
    a = p.parse_args()
    try:
        verify(json.loads(a.snapshot.read_text(encoding="utf-8")))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("FAIL CLOSED: " + str(exc), file=sys.stderr)
        return 1
    print("PASS: exact-head origin, ancestry, base and approved task verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
