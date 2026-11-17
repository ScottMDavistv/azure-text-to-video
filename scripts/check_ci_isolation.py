#!/usr/bin/env python3
"""Read-only workflow privilege audit. Execute the trusted main copy on PR YAML as DATA."""
import argparse
import pathlib
import re

GATE = ".github/workflows/pr-isolation-gate.yml"

def events(document):
    value = document.get("on")
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return set(value)
    if isinstance(value, str):
        return {value}
    return set()

def main_only(expression):
    return isinstance(expression, str) and "github.ref" in expression and "refs/heads/main" in expression

def excludes_pr(expression):
    if not isinstance(expression, str):
        return False
    value = re.sub(r"\s+", "", expression).replace('"', "'")
    return ("github.event_name!='pull_request'" in value or
            "github.event_name=='workflow_dispatch'" in value)

def elevated(permissions):
    if not isinstance(permissions, dict):
        return True
    return any(str(v).lower() not in ("read", "none") for v in permissions.values())

def examine(path, document, raw=""):
    problems = []
    if not isinstance(document, dict):
        return [path + ": expected workflow mapping"]
    ev = events(document)
    if not ev:
        problems.append(path + ": missing events")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return problems + [path + ": missing jobs"]
    permissions = document.get("permissions")
    if not isinstance(permissions, dict):
        problems.append(path + ": explicit permissions required")
        permissions = {}
    pr = "pull_request" in ev
    target = "pull_request_target" in ev
    if pr and elevated(permissions):
        problems.append(path + ": untrusted PR workflow has write permission")
    # An unreviewed PR may otherwise exfiltrate a repository Actions secret.
    if (pr or target) and re.search(r"[$][{][{]\s*secrets[.]", raw):
        problems.append(path + ": untrusted workflow references GitHub secrets")
    if target:
        if path != GATE or pr or permissions != {"contents": "read"}:
            problems.append(path + ": unexpected or privileged pull_request_target")
        if list(jobs) != ["audit"]:
            problems.append(path + ": trusted PR-data gate must contain only audit")
    for name, job in jobs.items():
        where = path + " job " + name
        if not isinstance(job, dict):
            problems.append(where + ": invalid job")
            continue
        condition = job.get("if", "")
        can_run_on_pr = not (main_only(condition) and excludes_pr(condition))
        job_permissions = job.get("permissions", permissions)
        if not isinstance(job_permissions, dict):
            problems.append(where + ": invalid permission mapping")
        if pr and can_run_on_pr and elevated(job_permissions):
            problems.append(where + ": PR job has token or OIDC write access")
        if pr and can_run_on_pr and job.get("environment"):
            problems.append(where + ": PR job may access protected environment")
        if pr and can_run_on_pr and "self-hosted" in str(job.get("runs-on", "")):
            problems.append(where + ": untrusted PR on self-hosted runner")
        if "workflow_dispatch" in ev and elevated(job_permissions) and not main_only(condition):
            problems.append(where + ": privileged dispatch is not limited to main")
        if "workflow_run" in ev and elevated(job_permissions) and not main_only(condition):
            problems.append(where + ": privileged workflow_run is not limited to main")
        if (pr and can_run_on_pr) or target:
            for step in job.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    if (step.get("with") or {}).get("persist-credentials") != "false":
                        problems.append(where + ": checkout may persist credentials")
                if target and step.get("run"):
                    cmd = str(step["run"])
                    if any(s in cmd for s in ("cd candidate", "./candidate", "candidate/")):
                        problems.append(where + ": PR checkout must never be executed")
        if target and (elevated(job_permissions) or job.get("environment")):
            problems.append(where + ": trusted PR gate cannot be privileged")
    return problems

def scan(root):
    import yaml  # Pinned PyYAML installed in the isolated scanner workflow.
    folder = pathlib.Path(root) / ".github/workflows"
    paths = sorted(folder.glob("*.yml")) + sorted(folder.glob("*.yaml"))
    if not paths:
        return ["No workflows found (fail closed)"]
    findings = []
    for file in paths:
        name = file.relative_to(root).as_posix()
        raw = file.read_text(encoding="utf-8")
        try:
            document = yaml.load(raw, Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            findings.append(name + ": invalid YAML")
            continue
        findings.extend(examine(name, document, raw))
    return findings

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    problems = scan(pathlib.Path(args.root).resolve())
    for item in problems:
        print("::error::" + item)
    if problems:
        raise SystemExit(1)
    print("PR workflow isolation policy passed (static review only)")
    print("Keep Copilot workflow approvals ON until repository secrets and main-only environment restrictions are verified.")

if __name__ == "__main__":
    main()
