#!/usr/bin/env python3
"""Read-only, fail-closed planning for independent security-review remediation.

This module has NO API, dispatch, secret, workflow, or merge side effects.
An independently reviewed protected-main controller must own eventual writes.
"""
import hashlib
import json
import re

SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REVIEWERS = frozenset({"copilot-pull-request-reviewer"})
MARKER_PREFIX = "security-remediation:v1:"


def sensitive(paths, *, complete=False):
    """Unknown or incomplete file inventory ALWAYS requires security review."""
    if not complete or not isinstance(paths, list) or not paths:
        return True
    blocked = ("finance", "auth", "identity", "oidc", "secret", "security",
               "credential", "deploy", "azure", "infra", "provider",
               "budget", "payment", "worker", "workflow", "policy", "api")
    return any(not isinstance(p, str) or
               any(word in p.lower() for word in blocked) or
               p.startswith(".github/") for p in paths)


def fingerprint(repo, number, path, body):
    """Stable ID for deduplication; no shell interpolation or dynamic code."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise ValueError("Invalid repository")
    if not isinstance(number, int) or number <= 0:
        raise ValueError("Invalid PR number")
    if not isinstance(path, str) or not path or len(path) > 512:
        raise ValueError("Invalid finding path")
    if not isinstance(body, str) or not body.strip() or len(body) > 30000:
        raise ValueError("Invalid finding")
    normalized = " ".join(body.casefold().split())
    source = json.dumps([repo.casefold(), number, path, normalized], ensure_ascii=True)
    return MARKER_PREFIX + hashlib.sha256(source.encode()).hexdigest()[:24]


def plan(pr, reviews, existing_issues, *, reviewer_login=None, reviewer_type=None):
    """Return bounded remediation issue candidates with no side effects.

    Reviews must be API-authenticated objects from protected GitHub endpoints,
    not markdown/issue text or arbitrary user-provided instructions.
    """
    head = (pr.get("head") or {}).get("sha", "")
    if (pr.get("state") != "open" or not pr.get("draft") or
            not SHA.fullmatch(head) or
            (pr.get("base") or {}).get("ref") != "main"):
        return {"state": "hold", "reason": "Unverified draft/main/head", "tasks": []}
    if reviewer_login not in SAFE_REVIEWERS or reviewer_type != "Bot":
        return {"state": "hold", "reason": "Review identity not independently verified", "tasks": []}
    if not isinstance(reviews, list) or not isinstance(existing_issues, list):
        return {"state": "hold", "reason": "Incomplete review or issue inventory", "tasks": []}
    markers = "\n".join(str(i.get("body") or "") for i in existing_issues
                       if isinstance(i, dict) and not i.get("pull_request"))
    tasks = []
    for item in reviews:
        if not isinstance(item, dict) or item.get("commit_id") != head:
            continue
        if item.get("state") not in ("CHANGES_REQUESTED", "COMMENTED"):
            continue
        if (item.get("user") or {}).get("login") != reviewer_login or (
                item.get("user") or {}).get("type") != reviewer_type:
            continue
        comments = item.get("comments", [])
        if not isinstance(comments, list):
            return {"state": "hold", "reason": "Unverified review comments", "tasks": []}
        for finding in comments:
            if not isinstance(finding, dict) or not finding.get("blocking"):
                continue
            path, body = finding.get("path"), finding.get("body")
            try:
                marker = fingerprint(pr["base"]["repo"]["full_name"],
                                     pr["number"], path, body)
            except (ValueError, KeyError, TypeError):
                return {"state": "hold", "reason": "Malformed review finding", "tasks": []}
            if marker in markers or any(t["marker"] == marker for t in tasks):
                continue
            tasks.append({"marker": marker, "pr": pr["number"], "head_sha": head,
                          "path": path, "finding": body[:30000],
                          "action": "would-create-remediation-issue",
                          "assignment": "held-until-separate-coder-and-security-gates"})
    return {"state": "review-findings" if tasks else "no-new-findings",
            "reason": "Dry-run only; no approval or agent dispatch", "tasks": tasks}


def can_dispatch_coder(*, active_agents, reviewer_active, isolated_secrets,
                       exact_head_review, included_credits_verified,
                       paid_overages_enabled, independent_coder):
    """Dispatch authorization is a guard proposal, NOT an implementation."""
    return (isinstance(active_agents, int) and 0 <= active_agents < 2 and
            reviewer_active is True and isolated_secrets is True and
            exact_head_review is True and included_credits_verified is True and
            paid_overages_enabled is False and independent_coder is True)


# Owner-authorized, decision-only queue policy. This remains a pure planner.
PRIORITY_ORDER = {"critical-security": 0, "high-security": 1,
                  "other-security": 2, "ordinary": 3}
ESCALATION_REASONS = frozenset({
    "owner-permission", "privileged-secret-migration", "finance-or-overage",
    "deployment-approval", "iam-change", "risk-acceptance",
    "disputed-security-finding", "repair-attempts-exhausted",
    "unsupported-capability", "security-sensitive-merge",
})


def ordered_queue(items):
    """Prioritize security remediation; reject unknown classes rather than downgrade."""
    if not isinstance(items, list):
        raise ValueError("Incomplete task queue")
    normalized = []
    for task in items:
        if not isinstance(task, dict) or task.get("priority_class") not in PRIORITY_ORDER:
            raise ValueError("Untrusted task priority")
        if not isinstance(task.get("id"), str) or not task["id"]:
            raise ValueError("Missing task id")
        normalized.append(task)
    return sorted(normalized, key=lambda x: (PRIORITY_ORDER[x["priority_class"]], x["id"]))


def next_assignable(items, *, active_agents, capacity_verified, security_gates_verified,
                    included_credits_verified, paid_overages_enabled):
    """No actual assignment; a blocked P0 security task stalls ordinary dispatch."""
    queue = ordered_queue(items)
    if (not isinstance(active_agents, int) or active_agents < 0 or active_agents >= 2
            or capacity_verified is not True or security_gates_verified is not True
            or included_credits_verified is not True or paid_overages_enabled is not False):
        return {"action": "hold", "reason": "Unverified agent/approval/credit capacity"}
    pending_security = [x for x in queue if x["priority_class"] != "ordinary"
                        and x.get("state") in ("queued", "blocked", "awaiting-review")]
    if pending_security:
        task = pending_security[0]
        if task.get("state") != "queued":
            return {"action": "hold", "reason": "Security work awaiting independent gate",
                    "task_id": task["id"]}
        if task.get("independent_reviewer") is not True or task.get("distinct_coder") is not True:
            return {"action": "hold", "reason": "Reviewer/coder separation unverified",
                    "task_id": task["id"]}
        return {"action": "would-dispatch-security", "task_id": task["id"]}
    ordinary = next((x for x in queue if x.get("state") == "queued"), None)
    return ({"action": "would-dispatch-ordinary", "task_id": ordinary["id"]}
            if ordinary else {"action": "idle"})


def should_notify_owner(event):
    """Do not notify about progress; only decisions requiring owner intervention."""
    if not isinstance(event, dict):
        return False
    return event.get("requires_owner_decision") is True and event.get("reason") in ESCALATION_REASONS
