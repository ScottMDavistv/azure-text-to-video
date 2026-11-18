#!/usr/bin/env python3
"""Fail-closed relay policy planner. No network, writes, dispatch or paid execution.

Inputs are trusted, freshly fetched GitHub snapshots supplied by a future adapter.
This offline planner NEVER treats PR descriptions/comments as authenticated facts.
An independently reviewed future adapter must check GitHub identities, source
provenance and branch creation attestation before honoring a suggested action.
"""
import argparse
import json
import re
from pathlib import Path

REPO = "ScottMDavistv/azure-text-to-video"
HEX = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED = frozenset({
    "Backend Python Tests", "Security Checks",
    "Design Management - Regression Tests",
    "Autonomous Engineering - Policy Tests",
    "Frontend Quality Gates", "Synthetic 300-second MP4 Acceptance",
    "Azure Read-Only Operations",
})


def _sha(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def check_coding_pr(task, target, coding):
    """Return *proposed* correction; never update a base or rewrite a branch.

    base auto-retarget is safe only when branch creation was independently
    attested at exact target head AND the same head is still current.
    """
    if task.get("repository") != REPO or target.get("repository") != REPO or coding.get("repository") != REPO:
        return {"action": "block", "reason": "repository mismatch"}
    if not all(_sha(v) for v in (task.get("pinned_target_sha"), target.get("head_sha"), coding.get("origin_sha"))):
        return {"action": "block", "reason": "invalid or missing SHA attestation"}
    if (task.get("target_pr") != target.get("number") or
            task.get("coding_pr") != coding.get("number") or
            not isinstance(target.get("head_branch"), str) or
            target.get("state") != "open" or coding.get("state") != "open" or not coding.get("draft")):
        return {"action": "block", "reason": "PR identity/state mismatch"}
    if target["head_sha"] != task["pinned_target_sha"]:
        return {"action": "block", "reason": "target moved; reissue pinned assignment"}
    if coding["origin_sha"] != target["head_sha"] or coding.get("origin_provenance") != "verified-creation-event":
        return {"action": "block", "reason": "wrong branch origin; recreate branch from target exact head"}
    if coding.get("base_branch") == target["head_branch"]:
        return {"action": "aligned", "reason": "base and origin pinned"}
    if coding.get("base_branch") == "main":
        return {"action": "propose_retarget", "expected_base": "main",
                "new_base": target["head_branch"],
                "expected_target_head": target["head_sha"],
                "reason": "retarget only after authenticated current-state compare-and-swap"}
    return {"action": "block", "reason": "unexpected base branch"}


def prepare_review(task, target, coding, ci):
    """Produce only a PENDING owner-approval review draft after exact-head CI.

    Source integrator must independently prove the approved implementation is
    incorporated (not just a branch or comment asserting it was merged).
    """
    if (not all(isinstance(x, dict) for x in (task, target, coding)) or
            task.get("repository") != REPO or target.get("repository") != REPO or
            coding.get("repository") != REPO):
        return {"state": "blocked", "reason": "repository mismatch"}
    if task.get("target_pr") != target.get("number") or not _sha(target.get("head_sha")):
        return {"state": "blocked", "reason": "invalid target identity/head"}
    if (target.get("state") != "open" or target.get("draft") is not True or
            target.get("base_branch") != "main"):
        return {"state": "blocked", "reason": "target must remain open draft against main"}
    if not task.get("approved_remediation_issue") or type(task["approved_remediation_issue"]) is not int:
        return {"state": "blocked", "reason": "missing authorized remediation issue"}
    if (type(task.get("coding_pr")) is not int or task["coding_pr"] < 1 or
            type(coding.get("number")) is not int or coding["number"] != task["coding_pr"] or
            type(coding.get("integration_target_pr")) is not int or
            coding["integration_target_pr"] != target["number"] or
            not _sha(coding.get("head_sha")) or
            coding.get("integrated_coding_head_sha") != coding["head_sha"] or
            coding.get("integrated_in_target") is not True or
            coding.get("integration_provenance") != "verified-git-ancestry" or
            coding.get("integrated_head_sha") != target["head_sha"]):
        return {"state": "blocked", "reason": "remediation not proven integrated into target"}
    previous = task.get("previous_review_head_sha")
    if not _sha(previous) or previous == target["head_sha"]:
        return {"state": "blocked", "reason": "no new target head since previous review"}
    # Applicability must be attested by a protected, trusted adapter. The
    # request's author cannot select a smaller required-check set.
    required = task.get("applicable_required_workflows")
    if (task.get("ci_policy_source") != "trusted-protected-task-ledger" or
            not isinstance(required, list) or not required or
            len(required) != len(set(required)) or
            not set(required).issubset(REQUIRED)):
        return {"state": "blocked", "reason": "required CI applicability unverified"}
    if not isinstance(ci, list) or not ci:
        return {"state": "blocked", "reason": "no CI evidence"}
    observed = set()
    for run in ci:
        if not isinstance(run, dict):
            return {"state": "blocked", "reason": "malformed CI evidence"}
        # Actor is NOT provenance: a legitimate run may be triggered by a user.
        # A trusted adapter must verify workflow file/path/ref from GitHub APIs.
        if (run.get("head_sha") != target["head_sha"] or
                run.get("status") != "completed" or run.get("conclusion") != "success" or
                run.get("trusted_workflow") is not True or
                run.get("provenance") != "verified-github-workflow-api" or
                not isinstance(run.get("workflow_path"), str) or
                not run["workflow_path"].startswith(".github/workflows/") or
                not isinstance(run.get("workflow_ref"), str) or
                not run["workflow_ref"].startswith("refs/heads/main@") or
                not _sha(run["workflow_ref"].split("@", 1)[-1])):
            return {"state": "blocked", "reason": "CI missing/stale/failed or untrusted"}
        if run.get("event") == "workflow_dispatch":
            if (run.get("manual_run_approved") is not True or
                    run.get("manual_approval_provenance") != "trusted-protected-task-ledger"):
                return {"state": "blocked", "reason": "manual CI approval unverified"}
        elif run.get("event") != "pull_request":
            return {"state": "blocked", "reason": "unexpected CI event"}
        if run.get("name") not in REQUIRED:
            return {"state": "blocked", "reason": "unexpected CI workflow"}
        observed.add(run["name"])
    if not set(required).issubset(observed):
        return {"state": "blocked", "reason": "applicable required CI not verified"}
    return {"state": "pending_owner_approval",
            "target_agent": "claude", "target_pr": target["number"],
            "head_sha": target["head_sha"], "base_ref": target.get("base_branch"),
            "scope": "independent full-diff security re-review",
            "remediation_issue": task["approved_remediation_issue"],
            "dispatch_enabled": False,
            "note": "Do not start Claude, post an assignment, or merge without separately authenticated owner approval."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("branch", "review"))
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    record = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = (check_coding_pr(record["task"], record["target"], record["coding"])
              if args.mode == "branch" else
              prepare_review(record["task"], record["target"], record["coding"], record["ci"]))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
