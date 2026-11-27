#!/usr/bin/env python3
"""Fail-closed verification of the non-FAL Azure Container Apps worker."""
import json
import subprocess

RG = "rg-textvideo-dev"
JOB = "job-textvideo-worker-dev"
IDENTITY_SUFFIX = "/userAssignedIdentities/id-textvideo-worker-dev"

def az(*args):
    result = subprocess.run(["az", *args, "-o", "json"], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)

def main():
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    identity = job.get("identity", {}).get("userAssignedIdentities") or {}
    assert len(identity) == 1 and next(iter(identity)).lower().endswith(IDENTITY_SUFFIX.lower()), "worker identity mismatch"
    props = job["properties"]
    config = props["configuration"]
    assert config["triggerType"] == "Manual", "automatic worker execution forbidden"
    assert config["replicaRetryLimit"] == 0, "unexpected worker retries"
    assert config.get("secrets") in (None, []), "worker still has secrets"
    container = props["template"]["containers"][0]
    assert container.get("command") == ["python"], "worker command mismatch"
    assert container.get("args") == ["worker_runtime.py", "--check"], "inspection command mismatch"
    env = {entry["name"]: entry for entry in container.get("env", [])}
    assert "FAL_KEY" not in env, "FAL_KEY unexpectedly exposed"
    for key, expected in {
        "VIDEO_RENDER_PROVIDER": "disabled",
        "ENABLE_WORKER_CONSUMPTION": "false",
        "ENABLE_LIVE_VIDEO_RENDERING": "false",
    }.items():
        assert env.get(key, {}).get("value") == expected, f"{key} not safely disabled"
    print("PASS: dedicated worker identity, no secrets, manual check-only, FAL disabled")

if __name__ == "__main__":
    main()
