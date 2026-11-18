#!/usr/bin/env python3
"""Offline approval inbox and corrective-task draft generator.

No GitHub writes or agent dispatch. Future trusted publisher must verify
actor IDs, current PR refs, SHA digest, replay/nonce, budget and deduplication.
"""
import hashlib
import json

REPO = "ScottMDavistv/azure-text-to-video"
KINDS = {"claude_review", "remediation", "security_merge", "branch_correction"}
SHA = __import__("re").compile(r"[0-9a-f]{40}\Z")


def queue_item(kind, pr, head_sha, title, details, issue_number=None):
    if kind not in KINDS or type(pr) is not int or pr < 1:
        raise ValueError("unsupported queue item")
    if not isinstance(head_sha, str) or not SHA.fullmatch(head_sha):
        raise ValueError("invalid exact head")
    if not isinstance(title, str) or not title.strip() or not isinstance(details, str) or not details.strip():
        raise ValueError("missing content")
    if issue_number is not None and (type(issue_number) is not int or issue_number < 1):
        raise ValueError("invalid linked issue")
    identity = {"repository": REPO, "kind": kind, "pr_number": pr,
                "head_sha": head_sha, "issue_number": issue_number}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return {**identity, "id": digest[:24], "title": title, "details": details,
            "status": "pending_owner_approval", "dispatch_enabled": False,
            "approval_provenance": None}


def corrective_task(coding_pr, target_pr, target_head_sha, reason):
    """Prepare task description only, keyed for publisher idempotency."""
    if type(target_pr) is not int or target_pr < 1 or target_pr == coding_pr:
        raise ValueError("invalid target PR")
    item = queue_item("branch_correction", coding_pr, target_head_sha,
                      "Correct coding PR ancestry for target PR #" + str(target_pr),
                      reason)
    # The target PR is part of the stable task identity; otherwise corrections
    # for different target PRs collide when coding PR/head happen to match.
    identity = {"repository": REPO, "kind": item["kind"],
                "coding_pr": coding_pr, "target_pr": target_pr,
                "target_head_sha": target_head_sha}
    item["id"] = hashlib.sha256(json.dumps(identity, sort_keys=True,
                          separators=(",", ":")).encode()).hexdigest()[:24]
    item["target_pr"] = target_pr
    item["dedupe_marker"] = "<!-- relay-branch-correction:" + item["id"] + " -->"
    item["issue_body"] = (
        item["dedupe_marker"] + "\n"
        "## Pending corrective task — no coding dispatch\n"
        "Coding PR #" + str(coding_pr) + " must be recreated/aligned from target PR #"
        + str(target_pr) + " exact SHA `" + target_head_sha + "`.\n"
        "Reason: " + reason + "\n"
        "Do not force-push, rewrite protected refs, merge, deploy, enable workflows, or incur paid use.\n"
        "Owner approval required for any outgoing coding-agent assignment.\n")
    return item


def consolidate(items):
    """Stable unified inbox; never auto-approves, dispatches or merges."""
    if not isinstance(items, list):
        raise ValueError("invalid inbox")
    keyed = {}
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in KINDS or item.get("status") != "pending_owner_approval":
            raise ValueError("invalid pending item")
        if item.get("dispatch_enabled") is not False or item.get("approval_provenance") is not None:
            raise ValueError("attempt to smuggle approved/active item")
        if item["id"] in keyed and keyed[item["id"]] != item:
            raise ValueError("conflicting duplicate")
        keyed[item["id"]] = item
    priority = {"branch_correction": 0, "remediation": 1, "claude_review": 2, "security_merge": 3}
    return sorted(keyed.values(), key=lambda v: (priority[v["kind"]], v["pr_number"], v["id"]))
