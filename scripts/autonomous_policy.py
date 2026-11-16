#!/usr/bin/env python3
"""Fail-closed policy for autonomous engineering. No repository writes.

Only a tiny, explicit low-risk surface qualifies for automatic merging.
Unknown changes, mixed-sensitivity changes and incomplete GitHub API responses
are always held for owner review. This is not a substitute for CI or code review.
"""
import fnmatch
import re

# These are intentionally narrow. Executable code, workflows, governance, IAM,
# finance, providers and infrastructure do NOT qualify for unattended merges.
LOW_RISK_GLOBS = (
    "docs/ui/*.md",
    "docs/user-guides/*.md",
    "frontend/src/*.css",
    "frontend/src/components/*.css",
)
BLOCKED_SEGMENTS = (
    "finance", "financial", "security", "secret", "credential", "provider",
    "render", "payment", "billing", "deploy", "infrastructure", "identity",
    "auth", "policy", "governance", "keyvault", "permission", "oidc",
    "copilot", "automation", "workflow", "budget",
)
MAX_FILES = 8
MAX_TOTAL_CHANGES = 350

def classify(files, *, complete=True):
    """Return deterministic result using GitHub list-pull-request-files records."""
    if not complete or not isinstance(files, list) or not files:
        return {"risk": "sensitive", "reason": "missing or incomplete file inventory"}
    if len(files) > MAX_FILES:
        return {"risk": "sensitive", "reason": "too many changed files"}
    changed = 0
    for file in files:
        path = file.get("filename", "")
        status = file.get("status")
        if (not isinstance(path, str) or not path or
                path.startswith("/") or "\\" in path or
                ".." in path.split("/") or
                any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", part) for part in path.split("/"))):
            return {"risk": "sensitive", "reason": "invalid path"}
        if status not in ("added", "modified"):
            return {"risk": "sensitive", "reason": "rename, removal or unknown status"}
        if any(s in path.lower() for s in BLOCKED_SEGMENTS):
            return {"risk": "sensitive", "reason": "protected path keyword"}
        if not any(fnmatch.fnmatchcase(path, glob) and path.count("/") == glob.count("/")
                   for glob in LOW_RISK_GLOBS):
            return {"risk": "sensitive", "reason": "path outside narrow allowlist"}
        additions, deletions = file.get("additions"), file.get("deletions")
        if not isinstance(additions, int) or not isinstance(deletions, int) or additions < 0 or deletions < 0:
            return {"risk": "sensitive", "reason": "missing diff statistics"}
        patch = file.get("patch")
        if not isinstance(patch, str) or not patch.startswith("@@") or len(patch) > 40000:
            return {"risk": "sensitive", "reason": "missing, binary, or oversized patch"}
        added_lines = "\n".join(
            line[1:].lower() for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++"))
        forbidden = ("@import", "url(", "javascript:", "<script",
                     "<iframe", "data:text/html", "${{", "onerror=",
                     "onload=", "<img")
        if any(token in added_lines for token in forbidden):
            return {"risk": "sensitive", "reason": "active external content in diff"}
        changed += additions + deletions
    if changed > MAX_TOTAL_CHANGES:
        return {"risk": "sensitive", "reason": "excessive diff size"}
    return {"risk": "low", "reason": "all files within restricted docs/CSS allowlist"}

def may_queue_auto_merge(pr, files, *, enabled, required_checks, protected_main_verified):
    if not enabled:
        return (False, "auto-merge disabled")
    if not protected_main_verified:
        return (False, "main protection unverified")
    if pr.get("draft") or pr.get("state") != "open":
        return (False, "PR not open/ready")
    if pr.get("base", {}).get("ref") != "main":
        return (False, "incorrect base")
    if pr.get("head", {}).get("repo", {}).get("full_name") != pr.get("base", {}).get("repo", {}).get("full_name"):
        return (False, "cross-repository head")
    author = (pr.get("user") or {}).get("login", "").lower()
    if author not in ("copilot-swe-agent[bot]", "copilot-swe-agent"):
        return (False, "not Copilot-authored")
    result = classify(files)
    if result["risk"] != "low":
        return (False, result["reason"])
    sha = pr.get("head", {}).get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        return (False, "head sha not verified")
    required = {"Backend Python Tests", "Security Checks"}
    if not required.issubset(required_checks):
        return (False, "exact-head required workflow results missing")
    return (True, "low-risk Copilot PR eligible for protected auto-merge")

def bounded_retries(attempt, *, limit=2):
    return isinstance(attempt, int) and 0 <= attempt < limit
