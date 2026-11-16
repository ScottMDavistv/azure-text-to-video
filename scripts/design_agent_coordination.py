#!/usr/bin/env python3
"""Issue-backed coordination; never executes code, merges PRs, or changes Azure.

Task definitions are owner-approved scope, stored in version-controlled JSON.
Default is dry-run. Publication is permitted only on default-branch scheduled audit.
"""
import datetime
import json
import os
import pathlib
import re
import urllib.parse
import urllib.request

AGENTS = ("frontend", "backend", "infrastructure", "integration")
PRIORITIES = ("P0", "P1", "P2")
ISSUE_PREFIX = "design-agent-task:"
ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "design-management-tasks.json"

def http_json(method, url, token, payload=None):
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "design-management-task-coordinator"}
    raw = None if payload is None else json.dumps(payload).encode()
    if raw is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def list_pages(repo, collection, token, limit=20):
    result = []
    for page in range(1, limit + 1):
        url = "https://api.github.com/repos/" + repo + "/" + collection
        sep = "&" if "?" in url else "?"
        batch = http_json("GET", url + sep + "per_page=100&page=" + str(page), token)
        if not isinstance(batch, list):
            raise RuntimeError("Invalid pagination response")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("Pagination exceeded; fail closed")

def validate(tasks):
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Expected nonempty task list")
    by_id = {}
    for task in tasks:
        key = task.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", key) or key in by_id:
            raise ValueError("Invalid/duplicate task ID: " + repr(key))
        if task.get("agent") not in AGENTS or task.get("priority") not in PRIORITIES:
            raise ValueError("Invalid agent/priority: " + key)
        if task.get("approval") != "approved-development":
            raise ValueError("Task lacks approved development scope: " + key)
        if task.get("sensitive") is not False:
            raise ValueError("Sensitive tasks must be handled manually: " + key)
        if not task.get("title") or not task.get("objective"):
            raise ValueError("Missing task definition: " + key)
        criteria = task.get("acceptance", [])
        if not isinstance(criteria, list) or not criteria or not all(isinstance(x, str) and x.strip() for x in criteria):
            raise ValueError("Missing acceptance: " + key)
        deps = task.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            raise ValueError("Invalid dependencies: " + key)
        by_id[key] = task
    for key, task in by_id.items():
        for dep in task.get("depends_on", []):
            if dep not in by_id or dep == key:
                raise ValueError("Missing/self dependency: " + key + " -> " + dep)
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError("Cyclic dependency")
        if key in visited:
            return
        visiting.add(key)
        for dep in by_id[key].get("depends_on", []):
            visit(dep)
        visiting.remove(key)
        visited.add(key)
    for key in by_id:
        visit(key)
    return by_id

def task_marker(key):
    return "<!-- " + ISSUE_PREFIX + key + " -->"

def issue_index(issues):
    indexed = {}
    for issue in issues:
        if issue.get("pull_request"):
            continue
        body = issue.get("body") or ""
        for key in re.findall(r"<!-- design-agent-task:([a-z][a-z0-9-]{2,63}) -->", body):
            if key in indexed:
                raise RuntimeError("Duplicate task marker exists in GitHub: " + key)
            indexed[key] = issue
    return indexed

def dependency_status(task, by_id, indexed):
    missing = []
    for dep in task.get("depends_on", []):
        prior = indexed.get(dep)
        if not prior or prior.get("state") != "closed" or prior.get("state_reason") != "completed":
            missing.append(dep)
    return missing

def work_queues(tasks, indexed):
    by_id = validate(tasks)
    queues = {agent: [] for agent in AGENTS}
    for task in tasks:
        issue = indexed.get(task["id"])
        missing = dependency_status(task, by_id, indexed)
        status = ("unpublished" if not issue else
                  "completed-recorded" if issue["state"] == "closed" and issue.get("state_reason") == "completed" else
                  "closed-unverified" if issue["state"] == "closed" else
                  "blocked" if missing else "ready")
        queues[task["agent"]].append({
            "id": task["id"], "priority": task["priority"], "status": status,
            "title": task["title"], "issue": issue.get("html_url") if issue else None,
            "dependencies_remaining": missing,
        })
    rank = {"P0": 0, "P1": 1, "P2": 2}
    for queue in queues.values():
        queue.sort(key=lambda item: (rank[item["priority"]], item["id"]))
    return queues

def issue_body(task):
    criteria = "\n".join("- [ ] " + item for item in task["acceptance"])
    deps = ", ".join(task.get("depends_on", [])) or "None"
    return "\n".join([
        task_marker(task["id"]),
        "## Development-only approved assignment",
        "**Agent:** " + task["agent"],
        "**Priority:** " + task["priority"],
        "**Objective:** " + task["objective"],
        "**Dependencies:** " + deps,
        "",
        "### Acceptance criteria",
        criteria,
        "",
        "### Completion evidence (required before any recommendation)",
        "- [ ] Linked PR on the expected repository and default branch",
        "- [ ] PR merged to main after separately authorized review",
        "- [ ] Exact merged commit has successful required CI checks",
        "- [ ] Evidence for this issue's specific acceptance criteria",
        "",
        "**Governance:** The task is approved to plan and implement development-only changes.",
        "No automatic merge, deployment, paid FAL activation, credential access, financial-policy change or self-approval.",
        "Sensitive changes require separate explicit owner authorization.",
        "Close as completed only after verified evidence; this coordinator does not close issues.",
    ])

def candidate_completion(issue, prs):
    """Conservative recommendation, not an approval. Requires a linked merged PR."""
    matches = []
    number = issue.get("number")
    for pr in prs:
        if not pr.get("merged_at") or pr.get("base", {}).get("ref") != "main":
            continue
        body = pr.get("body") or ""
        if re.search(r"(?<![0-9])(?:#|issues/)" + re.escape(str(number)) + r"(?![0-9])", body):
            matches.append(pr)
    return matches

def verify_completion(pr, runs, task):
    """Return review status from exact merged SHA only; never assert acceptance met."""
    sha = pr.get("merge_commit_sha")
    if not sha:
        return {"status": "missing-merge-sha", "evidence": []}
    relevant = [r for r in runs if r.get("head_sha") == sha and r.get("event") in ("push", "workflow_dispatch")]
    expected = {"Backend Python Tests", "Security Checks"}
    if task["agent"] == "integration":
        expected.add("Synthetic 300-second MP4 Acceptance")
    passed = {r.get("name") for r in relevant if r.get("status") == "completed" and r.get("conclusion") == "success"}
    missing = sorted(expected - passed)
    return {"status": "ci-review-ready" if not missing else "ci-unverified",
            "missing_checks": missing,
            "evidence": [r.get("html_url", "") for r in relevant if r.get("name") in expected
                         and r.get("status") == "completed" and r.get("conclusion") == "success"],
            "manual_acceptance_required": True}

def publish(repo, token, tasks, indexed, dry_run):
    changes = []
    for task in tasks:
        key = task["id"]
        if key in indexed:
            continue
        # Do not create a ready task before its dependencies are completed.
        outstanding = dependency_status(task, validate(tasks), indexed)
        if outstanding:
            changes.append({"id": key, "action": "hold", "dependencies": outstanding})
            continue
        changes.append({"id": key, "action": "create" if not dry_run else "would-create"})
        if not dry_run:
            item = http_json("POST", "https://api.github.com/repos/" + repo + "/issues", token,
                             {"title": "[Agent:" + task["agent"] + "] " + task["title"],
                              "body": issue_body(task)})
            indexed[key] = item
    return changes

def main():
    tasks = json.loads(MANIFEST.read_text(encoding="utf-8"))["tasks"]
    validate(tasks)
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise SystemExit("Missing GitHub credentials")
    issues = list_pages(repo, "issues?state=all", token)
    indexed = issue_index(issues)
    publish_enabled = (os.getenv("DESIGN_MANAGEMENT_PUBLISH") == "true"
                       and os.getenv("GITHUB_REF") == "refs/heads/main")
    changes = publish(repo, token, tasks, indexed, not publish_enabled)
    queues = work_queues(tasks, indexed)
    prs = list_pages(repo, "pulls?state=closed", token)
    candidates = []
    tasks_by_id = validate(tasks)
    for key, issue in indexed.items():
        if issue.get("state") != "open" or key not in {t["id"] for t in tasks}:
            continue
        for pr in candidate_completion(issue, prs):
            merged_sha = pr.get("merge_commit_sha", "")
            checks = []
            if re.fullmatch(r"[a-f0-9]{40}", merged_sha):
                response = http_json("GET", "https://api.github.com/repos/" + repo +
                                     "/actions/runs?head_sha=" + merged_sha + "&per_page=100", token)
                checks = response.get("workflow_runs", [])
            assessment = verify_completion(pr, checks, tasks_by_id[key])
            candidates.append({"task": key, "pr": pr["number"], **assessment})
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "publish_enabled": publish_enabled,
        "changes": changes, "queues": queues,
        "completion_candidates": candidates,
        "note": "No automatic closure or verification of acceptance; owner/CI evidence review required.",
    }
    output = pathlib.Path("design-management-queues.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
