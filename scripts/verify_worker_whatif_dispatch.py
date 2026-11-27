#!/usr/bin/env python3
"""Admit one exact owner dispatch, using authenticated GitHub run metadata."""
import datetime as dt
import json
import os
import re
import sys

OWNER = "ScottMDavistv"
OWNER_ID = 212811615
REPO_ID = 1457187422
REPO = "ScottMDavistv/azure-text-to-video"
WORKFLOW = ".github/workflows/guarded-azure-worker-deploy.yml"
TARGET = "rg-textvideo-dev/job-textvideo-worker-dev"
DIGEST = "sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646"
TTL_SECONDS = 3600

def deadline(run, env, now):
    sha, run_id = env["GITHUB_SHA"], env["GITHUB_RUN_ID"]
    if not (re.fullmatch(r"[0-9a-f]{40}", sha) and re.fullmatch(r"[1-9][0-9]*", run_id)):
        raise ValueError("invalid runtime context")
    expected_env = {
        "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main", "GITHUB_ACTOR": OWNER,
        "GITHUB_TRIGGERING_ACTOR": OWNER, "GITHUB_RUN_ATTEMPT": "1",
        "APPROVED_SHA": sha, "CONFIRMATION": "PREVIEW-DEV-WORKER",
        "APPROVED_TARGET": TARGET, "APPROVED_DIGEST": DIGEST,
    }
    if any(env.get(k) != v for k, v in expected_env.items()):
        raise ValueError("approval does not match this dispatch")
    expected_run = {"id": int(run_id), "run_attempt": 1, "event": "workflow_dispatch",
                    "head_branch": "main", "head_sha": sha}
    if run.get("path") not in (WORKFLOW, WORKFLOW + "@main", WORKFLOW + "@refs/heads/main"):
        raise ValueError("workflow source mismatch")
    if any(type(run.get(k)) is not type(v) or run.get(k) != v
           for k, v in expected_run.items()):
        raise ValueError("workflow run provenance mismatch")
    for key, field, expected in (("actor", "login", OWNER), ("triggering_actor", "login", OWNER),
                                ("repository", "full_name", REPO), ("head_repository", "full_name", REPO)):
        if not isinstance(run.get(key), dict) or run[key].get(field) != expected:
            raise ValueError("workflow run identity mismatch")
    if any(type(run[key].get("id")) is not int or run[key]["id"] != expected
           for key, expected in (("actor", OWNER_ID), ("triggering_actor", OWNER_ID),
                                 ("repository", REPO_ID), ("head_repository", REPO_ID))):
        raise ValueError("workflow run numeric identity mismatch")
    created = dt.datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    age = (now - created).total_seconds()
    if not 0 <= age < TTL_SECONDS:
        raise ValueError("approval expired or dated in the future")
    return int(created.timestamp()) + TTL_SECONDS

def main():
    try:
        expires = deadline(json.load(sys.stdin), os.environ, dt.datetime.now(dt.timezone.utc))
        print(expires)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        print("What-If admission refused.", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
