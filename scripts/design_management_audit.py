#!/usr/bin/env python3
"""Read-only project evidence collector with narrowly scoped GitHub Issue reporting.

No Azure credentials, provider keys, paid inference, or write APIs other than issue PATCH.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
START = "<!-- design-management-report:start -->"
END = "<!-- design-management-report:end -->"
CAPABILITIES = {
    "frontend authentication": ["frontend/src/entraAuth.ts", "frontend/src/App.tsx"],
    "frontend video editor": ["frontend/src/components/VideoEditor.tsx"],
    "backend job APIs": ["backend/main.py", "backend/jobs.py"],
    "durable dispatch": ["backend/dispatch_recovery.py", "backend/queue_lease.py"],
    "provider guardrails": ["backend/render_pipeline.py", "backend/worker_readiness.py"],
    "video assembly": ["backend/video_assembly.py"],
    "300-second end-to-end evidence": ["evidence/300-second-e2e.json"],
    "Azure infrastructure definitions": ["infra/main.bicep", "infra/worker-job.bicep"],
    "automated security checks": [".github/workflows/security-checks.yml"],
}
DASHBOARD_ISSUE = 16

def github_get(path, token, api=API):
    request = urllib.request.Request(
        api + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "video-render-design-auditor"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)

def github_patch(path, payload, token, api=API):
    request = urllib.request.Request(
        api + path, data=json.dumps(payload).encode("utf-8"), method="PATCH",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "video-render-design-auditor"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)

def capability_results(paths):
    present = set(paths)
    return [(name, all(p in present for p in required)) for name, required in CAPABILITIES.items()]

def replace_report(body, report):
    block = START + "\n" + report + "\n" + END
    if START in body and END in body and body.index(START) < body.index(END):
        return body[:body.index(START)] + block + body[body.index(END) + len(END):]
    return body.rstrip() + "\n\n" + block + "\n"

def status_for_run(run):
    return (run.get("name", "Unnamed workflow"), run.get("conclusion") or run.get("status", "unknown"),
            run.get("html_url", ""))

def render_report(data, now):
    cap = capability_results(data["paths"])
    p = [
        "### Automated evidence snapshot — " + now,
        "**Scope:** source inventory, recent CI and PRs, not a deployed product acceptance test.",
        "**Base commit:** `" + data["sha"] + "`",
        "**Product acceptance:** NOT VERIFIED — no validated 300-second video evidence.",
        "**Azure runtime:** NOT VERIFIED — this workflow has no Azure credentials.",
        "**Cost/provider activation:** NOT VERIFIED; no paid provider operations executed.",
        "",
        "#### Capability indicators (file existence only; NOT proof of functionality)",
    ]
    p += ["- " + ("Present" if found else "Missing / alternate implementation not verified") +
          ": " + name for name, found in cap]
    p += ["", "#### Recent CI (up to 10 runs)"]
    for name, status, url in data["runs"]:
        p.append("- [" + name.replace("[", "").replace("]", "") + "](" + url + "): " + status)
    if not data["runs"]:
        p.append("- No run evidence returned.")
    p += ["", "#### Open pull requests"]
    for number, title, url, draft in data["prs"]:
        title = title.replace("[", "").replace("]", "")
        p.append("- [#" + str(number) + " " + title + "](" + url + ")" + (" (draft)" if draft else ""))
    if not data["prs"]:
        p.append("- None returned.")
    p += ["", "#### Priority integration gates",
          "1. Build the actual prompt-to-video frontend and authenticated API integration.",
          "2. Prove clip planning, generation, download, stitching, and audio sync with non-billable fixtures.",
          "3. Validate 300-second MP4 duration and playback in end-to-end CI.",
          "4. Validate production-equivalent Azure runtime and observability separately with read-only credentials.",
          "5. Keep paid FAL disabled pending explicit activation and financial security gates.",
          "",
          "_Hybrid analysis: this deterministic evidence can be reviewed with ChatGPT on demand. No LLM API is called._"]
    return "\n".join(p)

def collect(repo, token):
    owner_repo = urllib.parse.quote(repo, safe="/")
    branch = github_get("/repos/" + owner_repo + "/branches/main", token)
    tree = github_get("/repos/" + owner_repo + "/git/trees/" + branch["commit"]["sha"] + "?recursive=1", token)
    if tree.get("truncated"):
        raise RuntimeError("Repository tree truncated; refusing incomplete audit")
    runs = github_get("/repos/" + owner_repo + "/actions/runs?branch=main&per_page=10", token)
    prs = github_get("/repos/" + owner_repo + "/pulls?state=open&per_page=50", token)
    return {
        "sha": branch["commit"]["sha"],
        "paths": [entry["path"] for entry in tree["tree"] if entry.get("type") == "blob"],
        "runs": [status_for_run(run) for run in runs.get("workflow_runs", [])][:10],
        "prs": [(pr["number"], pr.get("title", ""), pr.get("html_url", ""), pr.get("draft", False))
                for pr in prs],
    }

def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise SystemExit("Missing or invalid GitHub repository/token")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data = collect(repo, token)
    report = render_report(data, now)
    print(report)
    with open("design-management-report.md", "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    if os.environ.get("DESIGN_MANAGEMENT_PUBLISH") != "true":
        print("Dry run: issue update disabled")
        return
    path = "/repos/" + repo + "/issues/" + str(DASHBOARD_ISSUE)
    issue = github_get(path, token)
    new_body = replace_report(issue.get("body") or "", report)
    github_patch(path, {"body": new_body}, token)
    print("Updated dashboard issue #" + str(DASHBOARD_ISSUE))

if __name__ == "__main__":
    main()
