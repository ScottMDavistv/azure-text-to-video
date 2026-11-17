#!/usr/bin/env python3
"""Fail-closed GitHub Actions privilege isolation audit; PR YAML is data."""
import argparse
import pathlib
import re

GATE = ".github/workflows/pr-isolation-gate.yml"
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
REF = re.compile(r"""github\.ref\s*==\s*(['"])refs/heads/main\1""")
EVENT = re.compile(r"""github\.event_name\s*(?:==|!=)\s*(['"])[a-z_]+\1""")
FLAG = re.compile(r"""vars\.[A-Za-z_][A-Za-z0-9_]*\s*==\s*(['"])true\1""")
CONFIRM = re.compile(r"""inputs\.confirmation\s*==\s*(['"])DEPLOY-DEV\1""")
SECRET = re.compile(r"\bsecrets\b", re.I)
NAMED = re.compile(r"""\bsecrets(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['"]([^'"]+)['"]\s*\])""", re.I)

def events(document):
    value = document.get("on")
    return set(value) if isinstance(value, (dict, list)) else ({value} if isinstance(value, str) else set())

def expressions(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from expressions(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from expressions(v)
    elif isinstance(obj, str):
        for v in EXPR.finditer(obj):
            yield v.group(1)

def secret_refs(obj):
    out = set()
    for expression in expressions(obj):
        if SECRET.search(expression):
            refs = NAMED.findall(expression)
            out.update((x or y) for x, y in refs)
            if not refs or re.search(r"\bsecrets\b(?!\s*(?:\.|\[))", expression, re.I):
                out.add("<dynamic secret context>")
    return out

def privileged_secrets(obj):
    return secret_refs(obj) - {"GITHUB_TOKEN"}

def condition_terms(expr):
    """Only whole, positive, AND-conjoined literal guards are trusted."""
    if not isinstance(expr, str):
        return None
    src = expr.strip()
    if src.startswith("${{") and src.endswith("}}"):
        src = src[3:-2].strip()
    if not src or "||" in src or "!" in src.replace("!=", ""):
        return None
    parts = [p.strip() for p in src.split("&&")]
    return parts if all(p and any(pattern.fullmatch(p) for pattern in
                       (REF, EVENT, FLAG, CONFIRM)) for p in parts) else None

def main_only(expr):
    parts = condition_terms(expr)
    return bool(parts and any(REF.fullmatch(p) for p in parts))

def excludes_pr(expr):
    parts = condition_terms(expr)
    if not parts:
        return False
    return any(re.fullmatch(r"""github\.event_name\s*==\s*(['"])workflow_dispatch\1""", p)
               or re.fullmatch(r"""github\.event_name\s*!=\s*(['"])pull_request\1""", p)
               for p in parts)

def elevated(permissions):
    return not isinstance(permissions, dict) or any(
        str(v).lower() not in ("read", "none") for v in permissions.values())

def unsafe_push(document):
    on = document.get("on")
    if "push" not in events(document):
        return False
    # Scalar/list push triggers are valid and run on unrestricted branches.
    if not isinstance(on, dict):
        return True
    push = on["push"]
    return not (isinstance(push, dict) and push.get("branches") == ["main"]
                and not any(k in push for k in ("branches-ignore", "tags", "tags-ignore")))

def expected_gate():
    """Exact trusted-main structural allowlist; changing any field fails closed."""
    return {
        "name": "Trusted PR Workflow Isolation Gate",
        "on": {"pull_request_target":
               {"types": ["opened", "reopened", "synchronize", "ready_for_review"]}},
        "permissions": {"contents": "read"},
        "concurrency": {"group": "trusted-pr-isolation-${{ github.event.pull_request.number }}",
                        "cancel-in-progress": "true"},
        "jobs": {"audit": {
            "name": "Trusted workflow isolation (read-only)",
            "runs-on": "ubuntu-latest", "timeout-minutes": "10",
            "steps": [
                {"name": "Checkout trusted main scanner without persistent credentials",
                 "uses": CHECKOUT,
                 "with": {"ref": "main", "path": "trusted", "persist-credentials": "false"}},
                {"name": "Checkout untrusted PR files as inert data",
                 "uses": CHECKOUT,
                 "with": {"repository": "${{ github.event.pull_request.head.repo.full_name }}",
                          "ref": "${{ github.event.pull_request.head.sha }}",
                          "path": "candidate", "persist-credentials": "false"}},
                {"name": "Configure trusted Python", "uses": PYTHON,
                 "with": {"python-version": "3.12"}},
                {"name": "Install pinned YAML parser",
                 "run": "python -m pip install --disable-pip-version-check 'PyYAML==6.0.2'"},
                {"name": "Validate PR workflow YAML using only trusted code",
                 "run": "python trusted/scripts/check_ci_isolation.py --root candidate"},
            ]
        }}
    }

def examine(path, doc, raw=""):
    problems = []
    if not isinstance(doc, dict):
        return [path + ": invalid workflow"]
    triggers = events(doc)
    jobs = doc.get("jobs")
    if not triggers or not isinstance(jobs, dict) or not jobs:
        return [path + ": missing triggers or jobs"]
    perms = doc.get("permissions")
    if not isinstance(perms, dict):
        problems.append(path + ": explicit workflow permissions required")
        perms = {}
    pr, target = "pull_request" in triggers, "pull_request_target" in triggers
    feature = unsafe_push(doc)
    untrusted = pr or target or feature
    leaked = secret_refs(doc)
    # Also inspect the raw override used by focused unit tests.
    if raw:
        leaked.update(secret_refs({"raw": raw}))
    if untrusted and leaked:
        problems.append(path + ": untrusted secret context " + repr(sorted(leaked)))
    if untrusted and elevated(perms):
        problems.append(path + ": untrusted workflow has write/OIDC access")
    if target and (path != GATE or doc != expected_gate()):
        problems.append(path + ": trusted gate violates protected-main structural allowlist")
    for key, job in jobs.items():
        where = path + " job " + str(key)
        if not isinstance(job, dict):
            problems.append(where + ": invalid job")
            continue
        cond = job.get("if", "")
        local = job.get("permissions", perms)
        # Workflow-level env is inherited by every job; count it as effective privilege.
        secrets = privileged_secrets(job) | privileged_secrets(doc.get("env", {}))
        privileged = elevated(local) or bool(job.get("environment")) or bool(secrets)
        if secrets and not job.get("environment"):
            problems.append(where + ": custom secret requires a restricted environment")
        if privileged and (untrusted or "workflow_dispatch" in triggers or
                           "workflow_run" in triggers) and not main_only(cond):
            problems.append(where + ": privileged job must be guarded on main")
        if path == ".github/workflows/autonomous-engineering.yml":
            if secrets & {"COPILOT_DISPATCH_TOKEN", "AUTO_MERGE_USER_TOKEN"}:
                problems.append(where + ": legacy repository secret references forbidden")
            if "DM_COPILOT_DISPATCH_TOKEN" not in secrets:
                problems.append(where + ": controller requires dedicated environment token")
        if (pr and not (main_only(cond) and excludes_pr(cond))) or target or feature:
            if elevated(local):
                problems.append(where + ": untrusted job has write/OIDC token")
            if job.get("environment"):
                problems.append(where + ": untrusted job accesses environment")
            if "self-hosted" in str(job.get("runs-on", "")):
                problems.append(where + ": untrusted job on self-hosted runner")
            for step in job.get("steps", []) or []:
                if (isinstance(step, dict) and
                    str(step.get("uses", "")).startswith("actions/checkout@") and
                    (step.get("with") or {}).get("persist-credentials") != "false"):
                    problems.append(where + ": checkout persists credentials")
    return problems

def scan(root):
    import yaml
    directory = pathlib.Path(root) / ".github/workflows"
    paths = sorted(set(directory.glob("*.yml")) | set(directory.glob("*.yaml")))
    if not paths:
        return ["No workflows found"]
    problems = []
    for path in paths:
        try:
            data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            problems.append(str(path) + ": invalid YAML")
            continue
        problems.extend(examine(path.relative_to(root).as_posix(), data))
    return problems

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    results = scan(pathlib.Path(args.root).resolve())
    for message in results:
        print("::error::" + message)
    if results:
        raise SystemExit(1)
    print("Isolation policy passed; independent review and Environment safeguards still required.")

if __name__ == "__main__":
    main()
