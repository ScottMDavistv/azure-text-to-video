#!/usr/bin/env python3
"""Read-only, fail-closed planning for independent security-review remediation.

This module has NO API, dispatch, secret, workflow, or merge side effects.
An independently reviewed protected-main controller must own eventual writes.
"""
import hashlib
import json
import re

SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REVIEWERS = {"copilot-pull-request-reviewer[bot]": 175728472}
MARKER_PREFIX = "security-remediation:v1:"
LOCATION_MARKER_PREFIX = "security-remediation:v2:"
MARKER = re.compile(r"security-remediation:v[12]:[0-9a-f]{24}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _positive_int(value):
    return type(value) is int and value > 0


def _path(value):
    return (isinstance(value, str) and 0 < len(value) <= 512
            and all(part not in ("", ".", "..") and re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                    for part in value.split("/")))



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
                    or "\x00" in p or "\\" in p or p.startswith("/")
                    or any(part in ("", ".", "..") for part in p.split("/"))
                    or not (p.startswith("docs/") and p.endswith(".md"))
                    or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                               for part in p.split("/"))):
                return True
    return False


def fingerprint(repo, number, path, body, *, location=None):
    """Stable ID for deduplication; no shell interpolation or dynamic code."""
    if not isinstance(repo, str) or not REPOSITORY.fullmatch(repo):
        raise ValueError("Invalid repository")
    if not _positive_int(number):
        raise ValueError("Invalid PR number")
    if not _path(path):
        raise ValueError("Invalid finding path")
    if not isinstance(body, str) or not body.strip() or len(body) > 30000:
        raise ValueError("Invalid finding")
    normalized = " ".join(body.casefold().split())
    identity = [repo.casefold(), number, path, normalized]
    if location is not None:
        identity.append(location)
    source = json.dumps(identity, ensure_ascii=True)
    prefix = MARKER_PREFIX if location is None else LOCATION_MARKER_PREFIX
    return prefix + hashlib.sha256(source.encode()).hexdigest()[:24]


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
    In this offline module a callback is only a typed test seam; it cannot
    establish the identity of a production authority. Every result, even one
    based on a positive callback, is advisory and must not authorize anything.
    """
    hold = lambda reason: {"state": "hold", "reason": reason, "tasks": [], "advisory_only": True}
    if not isinstance(pr, dict) or not all(isinstance(pr.get(k), dict)
                                           for k in ("head", "base", "user")):
        return hold("Unverified PR")
    base_repo = pr["base"].get("repo")
    if not isinstance(base_repo, dict):
        return hold("Unverified repository")
    repo, number = base_repo.get("full_name"), pr.get("number")
    if not isinstance(repo, str) or not REPOSITORY.fullmatch(repo) or not _positive_int(number):
        return hold("Unverified repository or PR number")
    head = pr["head"].get("sha", "")
    if (pr.get("state") != "open" or pr.get("draft") is not True
            or not isinstance(head, str) or not SHA.fullmatch(head)
            or pr["base"].get("ref") != "main"):
        return hold("Unverified draft/main/head")
    if not isinstance(reviewer_login, str) or reviewer_login not in SAFE_REVIEWERS or reviewer_type != "Bot":
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
    reviewer_id = SAFE_REVIEWERS[reviewer_login]
    if (attested.get("repository") != repo or not _positive_int(attested.get("pr_number"))
            or attested["pr_number"] != number or attested.get("head_sha") != head
            or attested.get("reviewer_login") != reviewer_login
            or not _positive_int(attested.get("reviewer_id"))
            or attested["reviewer_id"] != reviewer_id):
        return hold("Evidence repository, PR, SHA or reviewer mismatch")
    coder = attested.get("coding_actor_login")
    authors = attested.get("commit_actor_logins")
    sessions = attested.get("coding_session_ids")
    review_session = attested.get("reviewer_session_id")
    owner = pr["user"].get("login")
    if (not isinstance(owner, str) or not owner.strip() or owner.casefold() == reviewer_login
            or not isinstance(coder, str) or not coder.strip()
            or not isinstance(authors, list) or not authors
            or not all(isinstance(a, str) and a and a.strip() == a for a in authors)
            or not isinstance(sessions, list) or not sessions
            or not all(isinstance(s, str) and s and s.strip() == s for s in sessions)
            or not isinstance(review_session, str) or not review_session
            or coder.strip() != coder or owner.strip() != owner or review_session.strip() != review_session
            or reviewer_login.casefold() in {coder.casefold(), *(a.casefold() for a in authors)}
            or review_session in sessions):
        return hold("Coder/reviewer independence unverified")
    # The trusted collector MUST check all commits, reviewer sessions, and
    # repository identities. PR author alone is not sufficient.
    if (attested.get("all_commit_actors_verified") is not True
            or attested.get("all_sessions_verified") is not True
            or attested.get("pagination_verified") is not True
            or attested.get("issue_authors_verified") is not True
            or attested.get("change_history_verified") is not True
            or attested.get("review_comment_bindings_verified") is not True
            or attested.get("review_status_history_verified") is not True):
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
    # A docs path does not prove a regular file (e.g. a symlink may end in .md).
    # Missing exact old/new file-type evidence is conservatively sensitive.
    if (attested.get("file_modes_verified") is not True or any(
            f.get("mode") not in ("100644", "100755")
            or (f.get("status") != "added" and f.get("previous_mode") not in ("100644", "100755"))
            for f in changed_files)):
        requires_security_review = True

    trusted_ids = attested.get("trusted_remediation_issue_ids")
    if not isinstance(trusted_ids, list) or not all(_positive_int(i) for i in trusted_ids):
        return hold("Authenticated remediation-issue inventory incomplete")
    trusted_ids = set(trusted_ids)
    matched_issue_ids, issue_states = set(), {}
    for issue in existing_issues:
        issue_id = issue.get("id")
        if not _positive_int(issue_id) or issue_id not in trusted_ids:
            continue
        marker = issue.get("marker")
        issue_state = issue.get("state")
        if (issue_id in matched_issue_ids or issue.get("pull_request")
                or issue.get("trusted_remediation") is not True
                or issue.get("author_assurance") != "trusted-controller"
                or issue.get("repository") != repo
                or not isinstance(marker, str) or not MARKER.fullmatch(marker)
                or issue_state not in ("open", "closed")):
            return hold("Malformed authenticated remediation issue")
        matched_issue_ids.add(issue_id)
        issue_states.setdefault(marker, set()).add(issue_state)
    if matched_issue_ids != trusted_ids:
        return hold("Authenticated remediation-issue inventory incomplete")

    tasks, findings, eligible, seen, seen_reviews = [], [], 0, {}, {}
    for item in reviews:
        if not isinstance(item, dict) or not _positive_int(item.get("id")):
            return hold("Malformed review evidence")
        review_id = item["id"]
        if review_id in seen_reviews:
            if seen_reviews[review_id] != item:
                return hold("Conflicting review identity or status")
            continue
        seen_reviews[review_id] = item
        state, commit = item.get("state"), item.get("commit_id")
        if state == "DISMISSED":
            # Only authenticated native dismissal, not a later clean review,
            # removes a change request. Status history belongs to the collector.
            continue
        if state == "PENDING":
            return hold("Review not complete")
        if state not in ("CHANGES_REQUESTED", "COMMENTED", "APPROVED"):
            return hold("Unknown review state")
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            return hold("Review target unknown")
        if commit != head:
            if state == "CHANGES_REQUESTED":
                return hold("Earlier change request not explicitly dismissed")
            # A finding on an earlier revision might still apply. This
            # offline planner cannot adjudicate its applicability.
            if item.get("comments") or item.get("body"):
                return hold("Earlier review findings require independent disposition")
            continue
        reviewer = item.get("user")
        comments, summary = item.get("comments"), item.get("body")
        if (not isinstance(reviewer, dict)
                or not _positive_int(reviewer.get("id")) or not isinstance(reviewer.get("login"), str)
                or not reviewer["login"] or reviewer.get("type") not in ("Bot", "User")
                or not isinstance(comments, list) or (summary is not None and not isinstance(summary, str))):
            return hold("Malformed exact-head review")
        if (isinstance(summary, str) and summary.strip()
                and attested.get("review_bodies_accounted_for") is not True):
            return hold("Review summary may contain unrepresented findings")
        if state == "CHANGES_REQUESTED" and not comments:
            return hold("Unrepresented changes-requested review")
        if (reviewer.get("login") != reviewer_login or reviewer.get("type") != reviewer_type
                or reviewer.get("id") != reviewer_id):
            if (reviewer.get("login", "").casefold() == reviewer_login.casefold()
                    or reviewer.get("id") == reviewer_id):
                return hold("Review identity mismatch")
            # No automated task may be raised on another actor's unverified
            # independence. Their substantive review still blocks clean output.
            if state == "CHANGES_REQUESTED" or comments or (summary and summary.strip()):
                return hold("Applicable other reviewer findings require independent disposition")
            continue
        eligible += 1
        for finding in comments:
            if not isinstance(finding, dict):
                return hold("Malformed review comment")
            comment_id, line = finding.get("id"), finding.get("line")
            path, body = finding.get("path"), finding.get("body")
            side = finding.get("side", "RIGHT")
            actor = finding.get("user")
            if (not _positive_int(comment_id) or not _positive_int(line)
                    or side not in ("LEFT", "RIGHT") or finding.get("commit_id") != head
                    or not _positive_int(finding.get("pull_request_review_id"))
                    or finding["pull_request_review_id"] != review_id
                    or not isinstance(actor, dict) or not _positive_int(actor.get("id"))
                    or actor["id"] != reviewer_id
                    or actor.get("login") != reviewer_login or actor.get("type") != reviewer_type):
                return hold("Unverified review comment binding or anchor")
            try:
                previous = fingerprint(repo, number, path, body)
                marker = fingerprint(repo, number, path, body, location=[side, line])
            except (ValueError, TypeError):
                return hold("Malformed review finding")
            if comment_id in seen:
                if seen[comment_id] != (review_id, marker):
                    return hold("Conflicting review comment identity")
                continue
            seen[comment_id] = (review_id, marker)
            if any(f["marker"] == marker for f in findings):
                continue
            if previous in issue_states:
                return hold("Legacy finding marker needs location reconciliation")
            issue_status = issue_states.get(marker, set())
            status = ("active" if "open" in issue_status else
                      "recurrence" if "closed" in issue_status else "new")
            # Keep only structured origins, no model-consumable review text.
            entry = {"marker": marker, "pr": number, "head_sha": head,
                     "path": path, "line": line, "side": side,
                     "review_id": review_id, "review_comment_id": comment_id,
                     "security_sensitive": requires_security_review, "status": status}
            findings.append(entry)
            if status == "new":
                tasks.append({**entry, "action": "would-create-remediation-issue",
                              "assignment": "held-until-separate-coder-and-security-gates"})
    if eligible == 0:
        return hold("No eligible exact-head review")
    statuses = {entry["status"] for entry in findings}
    state = ("recurrence-needs-attention" if "recurrence" in statuses else
             "review-findings" if tasks else
             "existing-active-findings" if findings else "no-new-findings")
    return {"state": state, "reason": "Dry-run only; no certification, approval or agent dispatch",
            "advisory_only": True, "findings": findings, "tasks": tasks}


def can_dispatch_coder(*, active_agents, reviewer_active, isolated_secrets,
                       exact_head_review, included_credits_verified,
                       paid_overages_enabled, independent_coder):
    """Advisory guard only. active_agents counts ALL active agents, including the reviewer."""
    return (type(active_agents) is int and active_agents == 1 and
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
    """Advisory only; active_agents counts all automated reviewers and coders."""
    queue = ordered_queue(items)
    if (type(active_agents) is not int or active_agents < 0 or active_agents >= 2
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
