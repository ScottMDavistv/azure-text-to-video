#!/usr/bin/env python3
"""Fail-closed relay policy planner. No network, writes, dispatch or paid execution.

Inputs are trusted, freshly fetched GitHub snapshots supplied by a future adapter.
This offline planner NEVER treats PR descriptions/comments as authenticated facts.
An independently reviewed future adapter must check GitHub identities, source
provenance and branch creation attestation before honoring a suggested action.
"""
import argparse
from copy import deepcopy
import json
import re
from pathlib import Path

REPO = "ScottMDavistv/azure-text-to-video"
HEX = re.compile(r"[0-9a-f]{40}\Z")
REPO_ID = 1457187422
WORKFLOWS = {
    "Backend Python Tests": "backend-tests.yml",
    "Security Checks": "security-checks.yml",
    "Design Management - Regression Tests": "design-management-tests.yml",
    "Autonomous Engineering - Policy Tests": "autonomous-policy-tests.yml",
    "Frontend Quality Gates": "frontend-quality-gates.yml",
    "Synthetic 300-second MP4 Acceptance": "synthetic-300s-acceptance.yml",
    "Azure Read-Only Operations": "azure-readonly-operations.yml",
}
REQUIRED = frozenset(WORKFLOWS)


def _sha(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None

def _positive_int(value):
    return type(value) is int and value > 0


def _repository(value):
    return (isinstance(value, dict) and type(value.get("id")) is int
            and value["id"] == REPO_ID
            and ("full_name" not in value or value["full_name"] == REPO))


def _distinct_prs(task, target, coding):
    numbers = (task.get("target_pr"), task.get("coding_pr"),
               target.get("number"), coding.get("number"))
    return (all(_positive_int(n) for n in numbers)
            and numbers[0] == numbers[2] and numbers[1] == numbers[3]
            and numbers[0] != numbers[1])


def _verified_run(run_id, name, target, lookup):
    """Validate authenticated adapter data, never assertions in the input draft.

    The callback is an explicit offline trust boundary, not an authenticator.
    It must independently read immutable Git objects and run/workflow metadata
    through trusted credentials, plus authenticated execution-source evidence.
    The CLI intentionally cannot supply such a callback.
    """
    path = ".github/workflows/" + WORKFLOWS[name]
    packet = deepcopy(lookup(run_id, path))
    run, workflow, execution = packet["run"], packet["workflow"], packet["execution"]
    head, main = target["head_sha"], packet["protected_main_sha"]
    event = run.get("event")
    if (not _sha(main) or run.get("id") != run_id
            or not _positive_int(run.get("id"))
            or not _positive_int(run.get("run_attempt"))
            or not _positive_int(run.get("workflow_id"))
            or not _positive_int(workflow.get("id"))
            or workflow["id"] != run["workflow_id"] or workflow.get("path") != path
            or run.get("name") != name or run.get("head_sha") != head
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or not _repository(run.get("repository"))
            or not _repository(run.get("head_repository"))
            or execution.get("run_id") != run_id or not _positive_int(execution.get("run_id"))
            or not _positive_int(execution.get("run_attempt"))
            or execution["run_attempt"] != run["run_attempt"]
            or not _sha(execution.get("workflow_sha"))):
        return False
    retrieved_sha = execution["workflow_sha"]
    if event == "pull_request":
        ref = f'refs/pull/{target["number"]}/merge'
        links = run.get("pull_requests")
        if not isinstance(links, list):
            return False
        same_pr = [row for row in links if isinstance(row, dict)
                   and _positive_int(row.get("number")) and row["number"] == target["number"]]
        if len(same_pr) != 1:
            return False
        link = same_pr[0]
        pr_head, base = link["head"], link["base"]
        if (pr_head.get("sha") != head or pr_head.get("ref") != target.get("head_branch")
                or not _repository(pr_head.get("repo")) or base.get("ref") != "main"
                or not _sha(base.get("sha")) or not _repository(base.get("repo"))
                or execution.get("merge_parents") != [base["sha"], head]):
            return False
        commits = (main, base["sha"], head, retrieved_sha)
    elif event == "workflow_dispatch":
        # Manual approval must be collected from a protected task ledger,
        # bound to the exact run attempt, workflow, target and reviewed head.
        branch = target.get("head_branch")
        if (not isinstance(branch, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", branch)
                or run.get("head_branch") != branch or retrieved_sha != head
                or packet.get("manual_approval") != {
                    "repository": REPO, "target_pr": target["number"],
                    "head_sha": head, "workflow_id": workflow["id"],
                    "run_id": run_id, "run_attempt": run["run_attempt"],
                }):
            return False
        ref = f"refs/heads/{branch}"
        commits = (main, head)
    else:
        return False
    if (run.get("path") not in (path, f"{path}@{ref}")
            or execution.get("workflow_ref") != f"{REPO}/{path}@{ref}"):
        return False
    # Exact blob equality across protected main, candidate and actual run
    # source, plus event-time base for PR runs. The adapter independently
    # obtains these path+commit Git tree entries; no task/run label can vouch
    # for its own workflow bytes. Symlinks and unknown objects do not qualify.
    source = packet["workflow_files"]
    blobs = []
    for commit in commits:
        entry = source[commit]
        if entry.get("mode") not in ("100644", "100755") or not _sha(entry.get("blob_sha")):
            return False
        blobs.append((entry["mode"], entry["blob_sha"]))
    return packet if len(set(blobs)) == 1 else False


def check_coding_pr(task, target, coding):
    """Return *proposed* correction; never update a base or rewrite a branch.

    base auto-retarget is safe only when branch creation was independently
    attested at exact target head AND the same head is still current.
    """
    if not all(isinstance(x, dict) and x.get("repository") == REPO
               for x in (task, target, coding)):
        return {"action": "block", "reason": "repository mismatch"}
    if not all(_sha(v) for v in (task.get("pinned_target_sha"), target.get("head_sha"), coding.get("origin_sha"))):
        return {"action": "block", "reason": "invalid or missing SHA attestation"}
    if (not _distinct_prs(task, target, coding)
            or not isinstance(target.get("head_branch"), str)
            or target.get("state") != "open" or coding.get("state") != "open"
            or coding.get("draft") is not True):
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


def verify_exact_head_ci(policy_source, applicable, ci, target, *, lookup_workflow_evidence=None):
    """Return (reason, packets), using only independently looked-up evidence.

    The caller supplies the exact target and policy from a trusted read path.
    The callback is an offline dependency, not authentication or activation.
    On success the returned packets are the same snapshots that were verified,
    so the queue can bind owner certification to evidence rather than claims.
    """
    if (not isinstance(target, dict) or target.get("repository") != REPO
            or not _positive_int(target.get("number"))
            or not _sha(target.get("head_sha"))
            or not isinstance(target.get("head_branch"), str) or not target["head_branch"]
            or target.get("base_branch") != "main"):
        return "invalid protected-main target identity/head", ()
    # Applicability must be attested by a protected, trusted adapter. The
    # request's author cannot select a smaller required-check set.
    required = applicable
    if (policy_source != "trusted-protected-task-ledger" or
            not isinstance(required, list) or not required or
            not all(isinstance(name, str) for name in required) or
            len(required) != len(set(required)) or
            not set(required).issubset(REQUIRED)):
        return "required CI applicability unverified", ()
    if not isinstance(ci, list) or not ci:
        return "no CI evidence", ()
    if not callable(lookup_workflow_evidence):
        return "trusted workflow evidence adapter unavailable", ()
    observed, run_ids, packets = set(), set(), []
    for claim in ci:
        if not isinstance(claim, dict):
            return "malformed CI evidence", ()
        run_id, name = claim.get("id"), claim.get("name")
        if (not _positive_int(run_id) or not isinstance(name, str)
                or name not in REQUIRED or run_id in run_ids):
            return "invalid workflow claim", ()
        try:
            verified = _verified_run(run_id, name, target, lookup_workflow_evidence)
        except Exception:
            verified = False
        if not verified:
            return "CI missing/stale/failed or untrusted", ()
        packets.append(verified)
        observed.add(name)
        run_ids.add(run_id)
    if not set(required).issubset(observed):
        return "applicable required CI not verified", ()
    return None, tuple(packets)


def prepare_review(task, target, coding, ci, *, lookup_workflow_evidence=None):
    """Produce only a PENDING owner-approval review draft after exact-head CI.

    Source integrator must independently prove the approved implementation is
    incorporated (not just a branch or comment asserting it was merged).
    """
    if (not all(isinstance(x, dict) for x in (task, target, coding)) or
            task.get("repository") != REPO or target.get("repository") != REPO or
            coding.get("repository") != REPO):
        return {"state": "blocked", "reason": "repository mismatch"}
    if not _distinct_prs(task, target, coding) or not _sha(target.get("head_sha")):
        return {"state": "blocked", "reason": "invalid target identity/head"}
    if (target.get("state") != "open" or target.get("draft") is not True or
            target.get("base_branch") != "main"):
        return {"state": "blocked", "reason": "target must remain open draft against main"}
    if not task.get("approved_remediation_issue") or type(task["approved_remediation_issue"]) is not int:
        return {"state": "blocked", "reason": "missing authorized remediation issue"}
    if (not _positive_int(coding.get("integration_target_pr")) or
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
    ci_reason, _ = verify_exact_head_ci(task.get("ci_policy_source"),
                                       task.get("applicable_required_workflows"), ci, target,
                                       lookup_workflow_evidence=lookup_workflow_evidence)
    if ci_reason is not None:
        return {"state": "blocked", "reason": ci_reason}
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
