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
REQUIREMENTS = "trusted/scripts/ci-isolation-requirements.txt"
# Every trigger outside this allowlist is denied (fail closed on new/unknown event types).
SUPPORTED = {"pull_request", "pull_request_target", "push", "workflow_dispatch",
             "workflow_call", "schedule", "workflow_run", "issues"}
# Default-branch events: github.ref is always main, so a main guard proves nothing
# about what code or payload the job handles.
BASE_CONTEXT = {"workflow_run", "issues", "schedule"}
TRUSTED_REFS = {"", "main", "refs/heads/main"}
EVENT_FIELD = re.compile(r"\bgithub\.event\b(?:\.[A-Za-z0-9_-]+|\[[^\]]*\])*", re.I)
SAFE_EVENT_FIELDS = {"github.event.pull_request.number", "github.event.pull_request.base.sha",
                     "github.event.pull_request.head.sha", "github.event.number"}
DYNAMIC_GITHUB = re.compile(r"\bgithub\b(?!\s*\.)|\bgithub\.head_ref\b", re.I)
PINNED_REMOTE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")

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

def injected_context(text):
    """Attacker-controllable event text interpolated into a shell/script is denied."""
    bad = set()
    for expression in EXPR.findall(text if isinstance(text, str) else ""):
        if DYNAMIC_GITHUB.search(expression):
            bad.add("<dynamic github context>")
        bad.update(f for f in EVENT_FIELD.findall(expression)
                   if f.lower() not in SAFE_EVENT_FIELDS)
    return bad

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
                {"name": "Install hash-pinned YAML parser",
                 "run": "python -m pip install --disable-pip-version-check --require-hashes "
                        "--only-binary=:all: --no-deps -r " + REQUIREMENTS},
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
    unsupported = triggers - SUPPORTED
    if unsupported:
        problems.append(path + ": unsupported trigger(s) " + repr(sorted(unsupported)))
    perms = doc.get("permissions")
    if not isinstance(perms, dict):
        problems.append(path + ": explicit workflow permissions required")
        perms = {}
    pr, target = "pull_request" in triggers, "pull_request_target" in triggers
    feature = unsafe_push(doc)
    # Reusable workflows run in the caller's context, which may be an untrusted PR.
    call = "workflow_call" in triggers
    base_context = bool(triggers & BASE_CONTEXT)
    untrusted = pr or target or feature or call
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
        if "uses" in job:
            if job.get("secrets") == "inherit":
                secrets.add("<inherited repository secrets>")
                problems.append(where + ": secrets: inherit forbidden; pass named secrets only")
            target_wf = str(job.get("uses"))
            if not target_wf.startswith("./.github/workflows/") and not PINNED_REMOTE.match(target_wf):
                problems.append(where + ": remote reusable workflow must be pinned to a commit SHA")
            if untrusted and job.get("secrets"):
                problems.append(where + ": untrusted reusable workflow call passes secrets")
        privileged = elevated(local) or bool(job.get("environment")) or bool(secrets)
        if secrets and not job.get("environment"):
            problems.append(where + ": custom secret requires a restricted environment")
        if privileged and (untrusted or "workflow_dispatch" in triggers or
                           "workflow_run" in triggers) and not main_only(cond):
            problems.append(where + ": privileged job must be guarded on main")
        steps = job.get("steps", []) or []
        if not isinstance(steps, list):
            problems.append(where + ": invalid steps")
            steps = []
        for step in steps:
            if not isinstance(step, dict):
                problems.append(where + ": invalid step")
                continue
            text = [step.get("run", "")]
            if str(step.get("uses", "")).lower().startswith("actions/github-script@"):
                text.append((step.get("with") or {}).get("script", ""))
            for chunk in text:
                bad = injected_context(chunk)
                if bad:
                    problems.append(where + ": untrusted event text in script " + repr(sorted(bad)))
            if str(step.get("uses", "")).lower().startswith("actions/checkout@") and \
                    (privileged or base_context) and path != GATE:
                opts = step.get("with") or {}
                ref = str(opts.get("ref", "")) if isinstance(opts, dict) else "<invalid>"
                # Only main or an immutable literal commit reviewed on main; never expressions.
                if not isinstance(opts, dict) or (ref not in TRUSTED_REFS and
                                                  not re.fullmatch(r"[0-9a-f]{40}", ref)) \
                        or "repository" in opts:
                    problems.append(where + ": privileged/base-context job checks out untrusted ref")
        if privileged and base_context:
            # workflow_run executes as main with main's environments; it must never
            # consume the triggering (possibly PR) run's code, refs or artifacts,
            # including via env/shell indirection that checkout-ref checks cannot see.
            if any(str(st.get("uses", "")).lower().startswith("actions/download-artifact@")
                   for st in steps if isinstance(st, dict)):
                problems.append(where + ": privileged base-context job downloads artifacts")
            if any(re.search(r"\bgithub\.event\.workflow_run\b", e, re.I) for e in expressions(job)):
                problems.append(where + ": privileged base-context job reads workflow_run payload")
        if path == ".github/workflows/autonomous-engineering.yml":
            if secrets & {"COPILOT_DISPATCH_TOKEN", "AUTO_MERGE_USER_TOKEN"}:
                problems.append(where + ": legacy repository secret references forbidden")
            if "DM_COPILOT_DISPATCH_TOKEN" not in secrets:
                problems.append(where + ": controller requires dedicated environment token")
        if ((pr or call) and not (main_only(cond) and excludes_pr(cond))) or target or feature:
            if elevated(local):
                problems.append(where + ": untrusted job has write/OIDC token")
            if job.get("environment"):
                problems.append(where + ": untrusted job accesses environment")
            if "self-hosted" in str(job.get("runs-on", "")):
                problems.append(where + ": untrusted job on self-hosted runner")
            for step in job.get("steps", []) or []:
                if (isinstance(step, dict) and
                    str(step.get("uses", "")).lower().startswith("actions/checkout@") and
                    (step.get("with") or {}).get("persist-credentials") != "false"):
                    problems.append(where + ": checkout persists credentials")
    return problems

def strict_load(yaml, text):
    """BaseLoader (no object construction) that also denies YAML GitHub may read
    differently: aliases/anchors, merge keys and duplicate keys."""
    if any(isinstance(e, yaml.AliasEvent) or getattr(e, "anchor", None)
           for e in yaml.parse(text, Loader=yaml.BaseLoader)):
        raise yaml.YAMLError("anchors/aliases are not supported by the isolation audit")

    class Strict(yaml.BaseLoader):
        def construct_mapping(self, node, deep=False):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key == "<<" or key in seen:
                    raise yaml.YAMLError("duplicate or merge key: " + repr(key))
                seen.add(key)
            return super().construct_mapping(node, deep)
    return yaml.load(text, Loader=Strict)

def scan(root):
    import yaml
    root = pathlib.Path(root)
    directory = root / ".github/workflows"
    if directory.is_symlink() or not directory.is_dir():
        return ["No workflows found"]
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in (".yml", ".yaml"))
    if not paths:
        return ["No workflows found"]
    problems = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            problems.append(name + ": workflow must be a regular file")
            continue
        try:
            data = strict_load(yaml, path.read_text(encoding="utf-8"))
            problems.extend(examine(name, data))
        except yaml.YAMLError:
            problems.append(name + ": invalid or unsupported YAML")
        except Exception as error:  # unanalyzable input is a denial, never a pass
            problems.append(name + ": audit failed closed (" + type(error).__name__ + ")")
    return problems

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    try:
        results = scan(pathlib.Path(args.root).resolve())
    except Exception as error:  # missing parser or unreadable tree: deny
        print("::error::isolation audit unavailable: " + type(error).__name__)
        raise SystemExit(2)
    for message in results:
        print("::error::" + message)
    if results:
        raise SystemExit(1)
    print("Isolation policy passed; independent review and Environment safeguards still required.")

if __name__ == "__main__":
    main()
