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
