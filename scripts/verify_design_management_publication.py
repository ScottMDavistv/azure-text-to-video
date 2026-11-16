"""Fail-closed check that the scheduled audit actually published its snapshot.

This script performs one read of issue #16; it never writes or calls Azure.
"""
import json
import os
import pathlib
import re
import urllib.request

REPORT = pathlib.Path("design-management-report.md")
MARKER = "<!-- design-management-report:start -->"
ISSUE = 16


def verify_published_snapshot(repo, token, report, *, fetch=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise ValueError("Valid repository and GitHub token required")
    if not report.startswith("### Automated evidence snapshot — "):
        raise ValueError("Audit snapshot missing or malformed")
    header = report.splitlines()[0]
    if fetch is None:
        url = f"https://api.github.com/repos/{repo}/issues/{ISSUE}"
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token,
                          "Accept": "application/vnd.github+json",
                          "User-Agent": "video-render-audit-verifier"})
        with urllib.request.urlopen(request, timeout=25) as response:
            fetch = json.load(response)
    body = fetch.get("body") or ""
    if MARKER not in body or header not in body:
        raise RuntimeError("Dashboard issue #16 does not contain this run's snapshot")
    return True


def main():
    text = REPORT.read_text(encoding="utf-8")
    verify_published_snapshot(
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_TOKEN", ""), text)
    print("PASS: issue #16 contains this exact audit snapshot")


if __name__ == "__main__":
    main()
