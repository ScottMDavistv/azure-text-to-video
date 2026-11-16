#!/usr/bin/env python3
"""Opt-in autonomous engineering controller. Runs trusted source on main only.

No paid coding agent activation unless separately configured with a user token.
No force/admin/direct merges. Never invokes any untrusted PR checkout code.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from autonomous_policy import classify, may_queue_auto_merge
from design_agent_coordination import (
    MANIFEST, dependency_status, issue_index, validate, work_queues,
)

API = "https://api.github.com"
COPILOT_BOT = "copilot-swe-agent[bot]"
MAX_CONCURRENT = 2
DECISION_MARKER = "design-management-decision:"
REQUIRED_CHECKS = {"Backend Python Tests", "Security Checks"}

def api(method, path, token, payload=None, *, attempts=3, sleep=time.sleep):
    if not token:
        raise RuntimeError("Missing explicitly authorized token")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "Authorization": "Bearer " + token, "User-Agent": "design-management-autonomy",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(API + path, method=method, headers=headers, data=data)
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            sleep(2 ** attempt)
        except (TimeoutError, urllib.error.URLError):
            # Mutating requests may have succeeded before a timeout. Never retry them.
            if method != "GET" or attempt == attempts - 1:
                raise
            sleep(2 ** attempt)

def pages(repo, endpoint, token, *, max_pages=15):
    result = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in endpoint else "?"
        page_data = api("GET", "/repos/" + repo + "/" + endpoint +
                        sep + "per_page=100&page=" + str(page), token)
        if not isinstance(page_data, list):
            raise RuntimeError("Unexpected pagination response; failing closed")
        result.extend(page_data)
        if len(page_data) < 100:
            return result
    raise RuntimeError("Pagination limit exceeded; failing closed")

def enabled(name):
    return os.environ.get(name, "").lower() == "true"

def authorized_context():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo):
        raise RuntimeError("Invalid repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Controller must run on main only")
    if not enabled("AUTONOMOUS_ENGINEERING_ENABLED"):
        return repo, False
    return repo, True

def active_slots(issues, prs):
    """Conservative upper bound; may double-count PRs linked to assigned issues."""
    bot_assigned = sum(1 for issue in issues
                       if issue.get("state") == "open"
                       and not issue.get("pull_request")
                       and any((u.get("login") or "").lower() in
                               ("copilot-swe-agent[bot]", "copilot-swe-agent")
                               for u in issue.get("assignees", [])))
    bot_prs = sum(1 for pr in prs if pr.get("state") == "open" and
                  (pr.get("user") or {}).get("login", "").lower() in
                  ("copilot-swe-agent[bot]", "copilot-swe-agent"))
    return bot_assigned + bot_prs

def dispatch_plan(tasks, indexed, *, slots):
    """No issue modification. Only existing approved, unblocked issues qualify."""
    if slots >= MAX_CONCURRENT:
        return []
    queues = work_queues(tasks, indexed)
    candidates = []
    by_id = validate(tasks)
    for agent in ("backend", "frontend", "integration", "infrastructure"):
        for entry in queues[agent]:
            if entry["status"] != "ready":
                continue
            task = by_id[entry["id"]]
            issue = indexed[entry["id"]]
            if issue.get("state") != "open" or issue.get("pull_request"):
                continue
            if any((x.get("login") or "").lower() in ("copilot-swe-agent[bot]", "copilot-swe-agent")
                   for x in issue.get("assignees", [])):
                continue
            # Explicitly exclude IAM, deployment and other security-sensitive work
            # even when someone labels a task non-sensitive.
            prohibited = ("azure-reader-monitoring",)
            if entry["id"] in prohibited:
                continue
            if task["sensitive"] is not False or task["approval"] != "approved-development":
                continue
            candidates.append((task["priority"], issue["number"], task))
    candidates.sort(key=lambda x: ({"P0": 0, "P1": 1, "P2": 2}[x[0]], x[1]))
    return candidates[:max(0, MAX_CONCURRENT - slots)]

def dispatch(repo, read_token, copilot_token, *, live=False):
    tasks = json.loads(MANIFEST.read_text(encoding="utf-8"))["tasks"]
    validate(tasks)
    issues = pages(repo, "issues?state=all", read_token)
    prs = pages(repo, "pulls?state=open", read_token)
    indexed = issue_index(issues)
    plan = dispatch_plan(tasks, indexed, slots=active_slots(issues, prs))
    output = [{"issue": number, "task": task["id"], "action": "would-dispatch"}
              for _, number, task in plan]
    if not live:
        return output
    if not copilot_token:
        raise RuntimeError("Copilot user-to-server token absent. Do not use GITHUB_TOKEN.")
    for _, number, task in plan:
        # GitHub REST Copilot assignment preview. No model or paid options specified.
        api("POST", "/repos/" + repo + "/issues/" + str(number) + "/assignees",
            copilot_token, {
                "assignees": [COPILOT_BOT],
                "agent_assignment": {
                    "target_repo": repo, "base_branch": "main",
                    "custom_instructions":
                        "Only implement this development-only task. Work on a short-lived PR. "
                        "Do not merge, deploy, modify financial/IAM/security/provider paths, "
                        "enable paid calls, or relax checks. Request owner decisions for blocked changes.",
                },
            }, attempts=1)
        output.append({"issue": number, "task": task["id"], "action": "dispatched"})
    return output

def workflow_evidence(repo, sha, token):
    response = api("GET", "/repos/" + repo +
                   "/actions/runs?head_sha=" + sha + "&per_page=100", token)
    runs = response.get("workflow_runs")
    if not isinstance(runs, list):
        raise RuntimeError("Missing CI run evidence")
    return {r.get("name") for r in runs
            if r.get("head_sha") == sha and r.get("status") == "completed"
            and r.get("conclusion") == "success"
            and r.get("event") in ("pull_request", "push")}

def auto_merge_plan(repo, token, *, protected_verified):
    prs = pages(repo, "pulls?state=open", token)
    result = []
    for summary in prs:
        number = summary["number"]
        pr = api("GET", "/repos/" + repo + "/pulls/" + str(number), token)
        # Guard against incomplete file enumeration. Pagination handles >100 files,
        # which is rejected by the risk classifier.
        files = pages(repo, "pulls/" + str(number) + "/files", token, max_pages=4)
        if len(files) > 8:
            checks = set()
        else:
            sha = (pr.get("head") or {}).get("sha", "")
            checks = workflow_evidence(repo, sha, token) if re.fullmatch(r"[a-f0-9]{40}", sha) else set()
        eligible, reason = may_queue_auto_merge(
            pr, files, enabled=True, required_checks=checks,
            protected_main_verified=protected_verified)
        # Decision triage must still work while auto-merge is disabled.
        risk = classify(files)
        author = (pr.get("user") or {}).get("login", "").lower()
        if not eligible and risk["risk"] != "low" and author in (
                "copilot-swe-agent[bot]", "copilot-swe-agent"):
            reason = "sensitive: " + risk["reason"]
        result.append({"number": number, "head_sha": (pr.get("head") or {}).get("sha"),
                       "eligible": eligible, "reason": reason})
    return result

def merge(repo, token, *, live=False, protected_verified=False):
    plan = auto_merge_plan(repo, token, protected_verified=protected_verified)
    if live:
        if not protected_verified or not enabled("AUTO_MERGE_ENABLED"):
            raise RuntimeError("Protected-main approval and auto-merge enablement required")
        if not os.environ.get("GH_TOKEN"):
            raise RuntimeError("Missing separate GitHub auto-merge token")
        settings = api("GET", "/repos/" + repo, token)
        if settings.get("allow_auto_merge") is not True:
            raise RuntimeError("Repository Allow auto-merge is not enabled")
        for entry in plan:
            if not entry["eligible"]:
                continue
            subprocess.run(["gh", "pr", "merge", str(entry["number"]),
                            "--repo", repo, "--auto", "--squash",
                            "--match-head-commit", entry["head_sha"]],
                           check=True, timeout=45)
            # Only one PR at a time; re-evaluate policy/CI/branch after merge.
            break
    return plan

def decision_body(code, title, details):
    return ("<!-- " + DECISION_MARKER + code + " -->\n"
            "## Owner decision required\n" + details + "\n\n"
            "No authorization to deploy, merge sensitive PRs, spend, weaken safeguards, "
            "or change identity/finance policies is implied.\n")

def decision_candidates(merge_plan):
    # Do not create a decision for every ordinary PR when automation is OFF or
    # branch rules cannot be verified. Escalate actual sensitive Copilot diffs only.
    ignored = ("not Copilot-authored", "PR not open/ready", "auto-merge disabled",
               "main protection unverified", "exact-head required workflow results missing")
    return [{"id": "sensitive-pr-" + str(x["number"]),
             "title": "Decision: PR #" + str(x["number"]) + " needs owner review",
             "details": "Automated merge blocked: " + x["reason"] +
                        ". Evaluate exact SHA " + str(x["head_sha"]) +
                        ", CI evidence, threat model and rollback before deciding."}
            for x in merge_plan if x["reason"] not in ignored and
            x["reason"] != "all files within restricted docs/CSS allowlist"]

def create_owner_decision(repo, token, code, title, details):
    marker = "<!-- " + DECISION_MARKER + code + " -->"
    existing = pages(repo, "issues?state=all", token)
    if any(marker in (item.get("body") or "") for item in existing
           if not item.get("pull_request")):
        return False
    api("POST", "/repos/" + repo + "/issues", token, {
        "title": title, "body": decision_body(code, title, details),
    }, attempts=1)
    return True

def decisions(repo, token, plan, *, live=False):
    existing = pages(repo, "issues?state=all", token)
    bodies = [i.get("body") or "" for i in existing if not i.get("pull_request")]
    created = []
    for candidate in decision_candidates(plan):
        marker = "<!-- " + DECISION_MARKER + candidate["id"] + " -->"
        if any(marker in body for body in bodies):
            continue
        created.append({"id": candidate["id"], "action": "would-create-decision"})
        if live:
            api("POST", "/repos/" + repo + "/issues", token, {
                "title": candidate["title"],
                "body": decision_body(candidate["id"], candidate["title"], candidate["details"]),
            }, attempts=1)
    return created

REPAIR_MARKER = "design-agent-repair:"
MAX_REPAIR_ATTEMPTS = 2

def repair_attempts(comments):
    attempts = set()
    for comment in comments:
        for value in re.findall(r"<!-- design-agent-repair:attempt:([12]) -->", comment.get("body") or ""):
            attempts.add(int(value))
    return attempts

def repair_decision(pr, runs, comments):
    """Returns a bounded, explicit Copilot repair plan, never a merge approval."""
    if pr.get("state") != "open" or pr.get("draft"):
        return {"action": "hold", "reason": "PR not ready"}
    if (pr.get("user") or {}).get("login", "").lower() not in (
            "copilot-swe-agent[bot]", "copilot-swe-agent"):
        return {"action": "hold", "reason": "not Copilot authored"}
    sha = (pr.get("head") or {}).get("sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        return {"action": "hold", "reason": "missing head sha"}
    relevant = [r for r in runs if r.get("head_sha") == sha and
                r.get("name") in REQUIRED_CHECKS and
                r.get("event") in ("pull_request", "push")]
    if not relevant:
        return {"action": "hold", "reason": "no current-head CI evidence"}
    # The most recent run for each check is authoritative. Any pending
    # or missing required workflow means the agent must not be re-prompted yet.
    latest = {}
    for run in sorted(relevant, key=lambda r: (r.get("created_at") or "", r.get("id") or 0)):
        latest[run["name"]] = run
    if set(latest) != REQUIRED_CHECKS or any(
            r.get("status") != "completed" for r in latest.values()):
        return {"action": "hold", "reason": "CI incomplete"}
    if all(r.get("conclusion") == "success" for r in latest.values()):
        return {"action": "hold", "reason": "CI green"}
    attempts = repair_attempts(comments)
    if any("<!-- design-agent-repair:head:" + sha + " -->" in (x.get("body") or "")
           for x in comments):
        return {"action": "hold", "reason": "already attempted current head"}
    if len(attempts) >= MAX_REPAIR_ATTEMPTS:
        return {"action": "escalate", "reason": "repair attempts exhausted"}
    return {"action": "request-repair", "attempt": min(set(range(1, MAX_REPAIR_ATTEMPTS + 1)) - attempts),
            "head_sha": sha}

def repair(repo, read_token, copilot_token, *, live=False):
    prs = pages(repo, "pulls?state=open", read_token)
    result = []
    for pr in prs:
        if (pr.get("user") or {}).get("login", "").lower() not in (
                "copilot-swe-agent[bot]", "copilot-swe-agent"):
            continue
        number = pr["number"]
        sha = (pr.get("head") or {}).get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            continue
        evidence = api("GET", "/repos/" + repo +
                       "/actions/runs?head_sha=" + sha + "&per_page=100", read_token)
        runs = evidence.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise RuntimeError("Incomplete workflow evidence")
        comments = pages(repo, "issues/" + str(number) + "/comments", read_token)
        decision = repair_decision(pr, runs, comments)
        if decision["action"] != "request-repair":
            result.append({"pr": number, **decision})
            if (live and decision["action"] == "escalate" and
                    enabled("DECISION_INBOX_ENABLED")):
                create_owner_decision(
                    repo, read_token, "repair-exhausted-pr-" + str(number),
                    "Decision: Copilot repair attempts exhausted on PR #" + str(number),
                    "Two bounded Copilot repair requests have been used. "
                    "Assess failing CI on current SHA " + sha + " and select a safe next step.")
            continue
        result.append({"pr": number, **decision, "live": live})
        if not live:
            continue
        if not copilot_token:
            raise RuntimeError("Copilot repair requires explicit user token")
        body = ("@copilot Please examine and repair the failed required CI on "
                "PR #" + str(number) + " at SHA " + sha +
                ". Work only within this PR's authorized development scope, "
                "preserve all security/financial controls and rerun CI. "
                "Do not merge, deploy, or enable paid providers.\n\n"
                "<!-- " + REPAIR_MARKER + "attempt:" + str(decision["attempt"]) +
                " -->\n<!-- " + REPAIR_MARKER + "head:" + sha + " -->")
        # Nonidempotent comment: one attempt, no automatic retry.
        api("POST", "/repos/" + repo + "/issues/" + str(number) + "/comments",
            copilot_token, {"body": body}, attempts=1)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dispatch", "merge", "decisions", "repair"), required=True)
    args = parser.parse_args()
    repo, enabled_all = authorized_context()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("Missing GitHub read token")
    if args.mode == "dispatch":
        live = enabled_all and enabled("COPILOT_DISPATCH_ENABLED")
        result = dispatch(repo, token, os.environ.get("COPILOT_DISPATCH_TOKEN", ""), live=live)
    elif args.mode == "repair":
        live = enabled_all and enabled("COPILOT_REPAIR_ENABLED")
        result = repair(repo, token, os.environ.get("COPILOT_DISPATCH_TOKEN", ""), live=live)
    elif args.mode == "merge":
        live = enabled_all and enabled("AUTO_MERGE_ENABLED")
        protected = enabled("PROTECTED_MAIN_VERIFIED")
        result = merge(repo, token, live=live, protected_verified=protected)
    else:
        plan = auto_merge_plan(repo, token, protected_verified=enabled("PROTECTED_MAIN_VERIFIED"))
        result = decisions(repo, token, plan,
                           live=enabled_all and enabled("DECISION_INBOX_ENABLED"))
    print(json.dumps({"mode": args.mode, "master_enabled": enabled_all,
                      "results": result}, indent=2))

if __name__ == "__main__":
    main()
