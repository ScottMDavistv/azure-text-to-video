"""Verify the exact current Actions audit report was published to issue #16.

One GitHub Issue GET only. Never writes or contacts Azure/providers.
"""
import json
import os
import pathlib
import re
import urllib.request

REPORT = pathlib.Path("design-management-report.md")
START = "<!-- design-management-report:start -->"
END = "<!-- design-management-report:end -->"
ISSUE = 16


def actions_identity(env):
    """Return a unique Actions execution identifier; fail closed if incomplete."""
    if env.get("GITHUB_ACTIONS", "").lower() != "true":
        return None
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    if not (re.fullmatch(r"[1-9][0-9]*", run_id)
            and re.fullmatch(r"[1-9][0-9]*", attempt)):
        raise ValueError("Missing valid GitHub Actions run ID/attempt")
    return "**GitHub Actions execution:** `" + run_id + "/" + attempt + "`"


def extract_published_report(body):
    """Only accept one complete and correctly ordered designated report block."""
    if body.count(START) != 1 or body.count(END) != 1:
        raise RuntimeError("Dashboard issue #16 report markers missing or duplicated")
    start = body.index(START) + len(START)
    end = body.index(END)
    if start >= end:
        raise RuntimeError("Dashboard issue #16 report markers are out of order")
    content = body[start:end]
    # Publication format adds exactly one wrapper newline on either side.
    if not content.startswith("\n") or not content.endswith("\n"):
        raise RuntimeError("Dashboard issue #16 report wrapper is malformed")
    return content[1:-1]


def verify_published_snapshot(repo, token, report, *, fetch=None, env=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise ValueError("Valid repository and GitHub token required")
    if not isinstance(report, str) or not report.startswith("### Automated evidence snapshot — "):
        raise ValueError("Audit snapshot missing or malformed")
    expected = actions_identity(os.environ if env is None else env)
    if expected is not None and report.count(expected) != 1:
        raise RuntimeError("Audit report belongs to a different GitHub Actions execution")
    if fetch is None:
        url = f"https://api.github.com/repos/{repo}/issues/{ISSUE}"
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token,
                          "Accept": "application/vnd.github+json",
                          "User-Agent": "video-render-audit-verifier"})
        with urllib.request.urlopen(request, timeout=25) as response:
            fetch = json.load(response)
    if not isinstance(fetch, dict):
        raise RuntimeError("Invalid dashboard issue response")
    body = fetch.get("body") or ""
    actual = extract_published_report(body)
    # The local report artifact can end in one newline; the wrapper's exact content cannot.
    if actual != report.removesuffix("\n"):
        raise RuntimeError("Dashboard issue #16 does not contain this run's exact report")
    return True


def main():
    report = REPORT.read_text(encoding="utf-8")
    verify_published_snapshot(
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_TOKEN", ""), report)
    print("PASS: issue #16 contains this exact audit report and execution identity")


if __name__ == "__main__":
    main()
