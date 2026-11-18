#!/usr/bin/env python3
"""Read-only, fail-closed planning for independent security-review remediation.

This module has NO API, dispatch, secret, workflow, or merge side effects.
An independently reviewed protected-main controller must own eventual writes.
"""
import hashlib
import json
import re

SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REVIEWERS = frozenset({"copilot-pull-request-reviewer[bot]"})
MARKER_PREFIX = "security-remediation:v1:"


def sensitive(paths, *, complete=False, previous_paths=None):
    """Only safe documentation changes qualify as low-risk; renames check both names.

    Callers MUST supply a complete old/new path inventory. If renames exist but
    previous_paths is unavailable the result is sensitive (fail closed).
    This classification never implies approval for unattended merging.
    """
    if complete is not True or not isinstance(paths, list) or not paths:
        return True
    if previous_paths is not None and (not isinstance(previous_paths, list)
                                       or len(previous_paths) != len(paths)):
        return True
    for index, path in enumerate(paths):
        candidates = [path]
        if previous_paths is not None:
            candidates.append(previous_paths[index])
        for p in candidates:
            if (not isinstance(p, str) or not p or len(p) > 512
                    or "\\x00" in p or "\\\\" in p or p.startswith("/")
                    or any(part in ("", ".", "..") for part in p.split("/"))
                    or not (p.startswith("docs/") and p.endswith(".md"))
                    or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                               for part in p.split("/"))):
                return True
    return False


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


def plan(pr, reviews, existing_issues, *, reviewer_login=None, reviewer_type=None,
         verified_evidence=None, verify_evidence=None, reviews_complete=False,
         comments_complete=False, issues_complete=False,
         changed_files=None, changed_files_complete=False):
    """Pure dry-run planner with a mandatory trusted-controller evidence boundary.

    verify_evidence must be supplied by a separately protected, authenticated
    main-only collector; caller-defined identity strings or booleans are NOT
    accepted as proof. The collector must independently authenticate every
    actor/session, the complete reviews/comments/issues, all commit actors and
    changed-file history, bind them to exact head, and verify its own trust root.
    No controller exists here: without one, HOLD is the only safe result.
    """
    hold = lambda reason: {"state": "hold", "reason": reason, "tasks": []}
    if not isinstance(pr, dict):
        return hold("Unverified PR")
    head = (pr.get("head") or {}).get("sha", "")
    if (pr.get("state") != "open" or pr.get("draft") is not True
            or not isinstance(head, str) or not SHA.fullmatch(head)
            or (pr.get("base") or {}).get("ref") != "main"):
        return hold("Unverified draft/main/head")
    if reviewer_login not in SAFE_REVIEWERS or reviewer_type != "Bot":
        return hold("Review identity not independently verified")
    if (reviews_complete is not True or comments_complete is not True
            or issues_complete is not True or changed_files_complete is not True):
        return hold("Pagination or changed-file history incomplete")
    if (not isinstance(reviews, list) or not isinstance(existing_issues, list)
            or any(not isinstance(i, dict) for i in existing_issues)
            or not isinstance(changed_files, list) or not changed_files):
        return hold("Incomplete review, issue or file inventory")
    # Review data and provenance strings are untrusted until a protected
    # independent collector validates them, including complete commit actors.
    if not callable(verify_evidence) or not isinstance(verified_evidence, dict):
        return hold("Trusted provenance verifier unavailable")
    try:
        attested = verify_evidence(verified_evidence, pr, reviews,
                                   existing_issues, changed_files)
    except Exception:
        return hold("Trusted evidence verification failed")
    if not isinstance(attested, dict) or attested.get("verified") is not True:
        return hold("Trusted evidence verification failed")
    if attested.get("head_sha") != head or attested.get("reviewer_login") != reviewer_login:
        return hold("Evidence SHA or reviewer mismatch")
    coder = attested.get("coding_actor_login")
    authors = attested.get("commit_actor_logins")
    sessions = attested.get("coding_session_ids")
    review_session = attested.get("reviewer_session_id")
    if (not isinstance(coder, str) or not coder
            or not isinstance(authors, list) or not authors
            or not all(isinstance(a, str) and a for a in authors)
            or not isinstance(sessions, list) or not sessions
            or not all(isinstance(s, str) and s for s in sessions)
            or not isinstance(review_session, str) or not review_session
            or reviewer_login.casefold() in {coder.casefold(), *(a.casefold() for a in authors)}
            or review_session in sessions):
        return hold("Coder/reviewer independence unverified")
    # The trusted collector MUST check all commits, reviewer sessions, and
    # repository identities. PR author alone is not sufficient.
    if (attested.get("all_commit_actors_verified") is not True
            or attested.get("all_sessions_verified") is not True
            or attested.get("pagination_verified") is not True
            or attested.get("issue_authors_verified") is not True
            or attested.get("change_history_verified") is not True):
        return hold("Authenticated evidence incomplete")
    file_paths = []
    previous_paths = []
    for changed in changed_files:
        if not isinstance(changed, dict):
            return hold("Malformed changed-file evidence")
        filename = changed.get("filename")
        previous = changed.get("previous_filename")
        if changed.get("status") == "renamed" and not isinstance(previous, str):
            return hold("Missing rename ancestry")
        file_paths.append(filename)
        previous_paths.append(previous if previous is not None else filename)
    requires_security_review = sensitive(file_paths, complete=True,
                                          previous_paths=previous_paths)
    # Dedup only against trusted controller-validated issue provenance and
    # exact marker, never substring matches or attacker-owned issue content.
    approved_markers = set()
    for issue in existing_issues:
        if issue.get("pull_request"):
            continue
        if (issue.get("trusted_remediation") is True
                and issue.get("author_assurance") == "trusted-controller"
                and issue.get("repository") == pr.get("base", {}).get("repo", {}).get("full_name")
                and isinstance(issue.get("marker"), str)
                and re.fullmatch(re.escape(MARKER_PREFIX) + r"[0-9a-f]{24}",
                                 issue["marker"])):
            approved_markers.add(issue["marker"])
    tasks, eligible = [], 0
    for item in reviews:
        if not isinstance(item, dict):
            return hold("Malformed review evidence")
        if item.get("commit_id") != head:
            continue
        if item.get("state") not in ("CHANGES_REQUESTED", "COMMENTED", "APPROVED"):
            continue
        reviewer = item.get("user")
        if (not isinstance(reviewer, dict)
                or reviewer.get("login") != reviewer_login
                or reviewer.get("type") != reviewer_type):
            continue
        eligible += 1
        comments = item.get("comments")
        if not isinstance(comments, list):
            return hold("Missing or unverified exact-head comments")
        if item.get("state") == "CHANGES_REQUESTED" and not comments:
            return hold("Unrepresented changes-requested review")
        if item.get("state") == "CHANGES_REQUESTED" and item.get("body") and not comments:
            return hold("Unrepresented review body")
        for finding in comments:
            # Real GitHub review comments have no 'blocking' field. Every
            # actionable inline comment is a finding; never discard by default.
            if not isinstance(finding, dict):
                return hold("Malformed review comment")
            path, body = finding.get("path"), finding.get("body")
            if not isinstance(body, str) or not body.strip():
                return hold("Malformed review comment")
            try:
                marker = fingerprint(pr["base"]["repo"]["full_name"],
                                     pr["number"], path, body)
            except (ValueError, KeyError, TypeError):
                return hold("Malformed review finding")
            if marker in approved_markers or any(t["marker"] == marker for t in tasks):
                continue
            tasks.append({"marker": marker, "pr": pr["number"],
                          "head_sha": head, "path": path, "finding": body[:30000],
                          "security_sensitive": requires_security_review,
                          "action": "would-create-remediation-issue",
                          "assignment": "held-until-separate-coder-and-security-gates"})
    if eligible == 0:
        return hold("No eligible exact-head review")
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
                        and x.get("state") != "completed"]
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
