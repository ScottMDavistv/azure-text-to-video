#!/usr/bin/env python3
"""Fail-closed GitHub Actions privilege isolation audit; PR YAML is data."""
import argparse
import hashlib
import pathlib
import re

GATE = ".github/workflows/pr-isolation-gate.yml"
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
def extract_expressions(text):
    """Read GitHub expressions without treating }} inside single quotes as closing.

    The runner accepts doubled single quotes inside expression strings. Unclosed
    template expressions are returned as malformed, so callers fail closed.
    """
    if not isinstance(text, str):
        return
    pos = 0
    while True:
        start = text.find("${{", pos)
        if start < 0:
            return
        i, quoted = start + 3, False
        while i < len(text):
            if text[i] == "'":
                if quoted and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quoted = not quoted
            elif not quoted and text.startswith("}}", i):
                yield text[start + 3:i], False
                pos = i + 2
                break
            i += 1
        else:
            yield text[start + 3:], True
            return

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
        for expression, malformed in extract_expressions(obj):
            yield expression

def all_strings(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from all_strings(key)
            yield from all_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from all_strings(value)
    elif isinstance(obj, str):
        yield obj

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
    for expression, malformed in extract_expressions(text):
        expression = re.sub(r"\s*\.\s*", ".", expression)
        if malformed:
            bad.add("<malformed github expression>")
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


# The existing deployed-worker workflow is the only consumer of these three
# compound guard forms. Match the complete expressions for their specific jobs;
# do not extend the generic PR/callee guard grammar or accept arbitrary tails.
GUARDED_AZURE = ".github/workflows/guarded-azure-worker-deploy.yml"

# Two byte-pinned exceptions, interpreted only from the audited workflow tree.
# The callee pin is the independently reviewed commit, not a mutable ref.
# A new caller or callee body requires a new scanner review.
PREVIEW_CALLEE = ".github/workflows/dev-worker-whatif-preview.yml"
PREVIEW_CALLEE_SHA = "6eb006aad90d8797aefed013b64edad17c72a5f3"
PREVIEW_CALLEE_BYTES = "6ec2ea16912b770e2026e977858b0b082d3e80428e092cabd20dfb3bac44ad09"
PREVIEW_CALLER_BYTES = "f866d39fa37feb991b9e5e2f311157b07cde2e63de7c9d4f6af6bc7735eff6d8"
PREVIEW_REF = f"ScottMDavistv/azure-text-to-video/{PREVIEW_CALLEE}@{PREVIEW_CALLEE_SHA}"
PREVIEW_ENV = "dev-worker-whatif-preview"


def reserved_preview_path(path):
    leaf = path.strip().replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return leaf in {PREVIEW_ENV + ".yml", PREVIEW_ENV + ".yaml"}


def environment_name(job):
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return str(env or "").strip()

_AZURE_MANUAL_MAIN = (
    "github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && "
)
_AZURE_OWNER = "github.actor == 'ScottMDavistv'"
_AZURE_PROTECTED = "vars.PROTECTED_MAIN_VERIFIED == 'true'"
_AZURE_GUARDS = {
    "preview": (
        _AZURE_MANUAL_MAIN
        + "(inputs.operation == 'preview' || inputs.operation == 'deploy') && "
        + _AZURE_OWNER,
        "dev-deployment",
    ),
    "whatif": (
        _AZURE_MANUAL_MAIN
        + "inputs.operation == 'deploy' && "
        + _AZURE_OWNER + " && " + _AZURE_PROTECTED,
        "dev-deployment-apply",
    ),
    "owner_whatif": (
        _AZURE_MANUAL_MAIN + "inputs.operation == 'whatif' && " + _AZURE_OWNER
        + " && github.triggering_actor == 'ScottMDavistv' && github.run_attempt == '1' && "
        + _AZURE_PROTECTED + " && vars.AZURE_DEV_WHATIF_ENABLED == 'true'",
        "dev-worker-whatif-preview",
    ),
    "deploy": (
        _AZURE_MANUAL_MAIN + "inputs.operation == 'deploy' && "
        + _AZURE_OWNER + " && inputs.approved_sha == github.sha && "
        + "inputs.confirmation == 'DEPLOY-DEV-WORKER' && "
        + "vars.AZURE_DEV_DEPLOY_ENABLED == 'true' && " + _AZURE_PROTECTED,
        "dev-deployment-apply",
    ),
}


def guarded_azure_job(key, job, triggers, feature, has_owner_whatif):
    known = _AZURE_GUARDS.get(key)
    # Legacy guards are compatible only if the dedicated route is absent.
    old_conditions = {
        "preview": _AZURE_MANUAL_MAIN
                   + "(inputs.operation == 'preview' || inputs.operation == 'whatif' || inputs.operation == 'deploy') && "
                   + _AZURE_OWNER,
        "whatif": _AZURE_MANUAL_MAIN
                  + "(inputs.operation == 'whatif' || inputs.operation == 'deploy') && "
                  + _AZURE_OWNER + " && " + _AZURE_PROTECTED,
    }
    return bool(
        known and not feature
        and "workflow_dispatch" in triggers
        and triggers <= {"push", "schedule", "workflow_dispatch"}
        and (job.get("if") == known[0]
             or (not has_owner_whatif and key in old_conditions
                 and job.get("if") == old_conditions[key]))
        and (key != "owner_whatif" or job.get("needs") == "whatif_admission")
        and job.get("environment") == known[1]
        and job.get("permissions") == {"contents": "read", "id-token": "write"}
    )


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
    return _examine(path, doc, raw)


def _examine(path, doc, raw="", *, exact_callee=False, exact_caller=False):
    problems = []
    if not isinstance(doc, dict):
        return [path + ": invalid workflow"]
    triggers = events(doc)
    jobs = doc.get("jobs")
    if not triggers or not isinstance(jobs, dict) or not jobs:
        return [path + ": missing triggers or jobs"]
    if reserved_preview_path(path):
        if path == PREVIEW_CALLEE and exact_callee:
            # Strict YAML loading and byte identity precede this exception.
            # No generic reusable-workflow or permission grammar is broadened.
            return []
        problems.append(path + ": reserved preview callee must match reviewed bytes")
    has_owner_whatif = "owner_whatif" in jobs
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
    inherited_context = base_context or call
    untrusted = pr or target or feature or call
    malformed = any(bad for value in all_strings(doc)
                    for _, bad in extract_expressions(value))
    if malformed:
        problems.append(path + ": malformed GitHub expression")
    leaked = secret_refs(doc)
    # Also inspect the raw override used by focused unit tests.
    if raw:
        leaked.update(secret_refs({"raw": raw}))
    if untrusted and leaked:
        problems.append(path + ": untrusted secret context " + repr(sorted(leaked)))
    if untrusted and elevated(perms):
        problems.append(path + ": untrusted workflow has write/OIDC access")
    if path == GUARDED_AZURE and perms != {"contents": "read"}:
        problems.append(path + ": guarded Azure workflow requires contents-read only")
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
        pinned_preview_call = (
            path == GUARDED_AZURE and exact_caller and key == "owner_whatif"
            and job.get("uses") == PREVIEW_REF
        )
        env_name = environment_name(job)
        if "${{" in env_name or env_name.casefold() == PREVIEW_ENV:
            problems.append(where + ": dedicated preview environment is reserved")
        if "uses" in job:
            call_ref = str(job["uses"]).strip()
            if "${{" in call_ref or (
                    reserved_preview_path(call_ref.split("@", 1)[0])
                    and not pinned_preview_call):
                problems.append(where + ": only the exact reviewed preview call is allowed")
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
        azure_manual = pinned_preview_call or (path == GUARDED_AZURE and guarded_azure_job(
            key, job, triggers, feature, has_owner_whatif))
        if path == GUARDED_AZURE and privileged and not azure_manual:
            problems.append(where + ": Azure privilege requires the exact manual job guard")
        if privileged and (untrusted or "workflow_dispatch" in triggers or
                           "workflow_run" in triggers) and not (main_only(cond) or azure_manual):
            problems.append(where + ": privileged job must be guarded on main")
        steps = job.get("steps", []) or []
        if not isinstance(steps, list):
            problems.append(where + ": invalid steps")
            steps = []
        for step in steps:
            if (path == GUARDED_AZURE and isinstance(step, dict)
                    and str(step.get("uses", "")).strip().lower().startswith("azure/login@")
                    and not azure_manual):
                problems.append(where + ": Azure login requires the exact manual job guard")
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
        if inherited_context:
            # workflow_run executes as main with main's environments; it must never
            # consume the triggering (possibly PR) run's code, refs or artifacts,
            # including via env/shell indirection that checkout-ref checks cannot see.
            if any("download-artifact" in str(st.get("uses", "")).lower()
                   for st in steps if isinstance(st, dict)):
                problems.append(where + ": privileged base-context job downloads artifacts")
            if any(injected_context(v) for v in all_strings({
                    "job": job, "env": doc.get("env"),
                    "defaults": doc.get("defaults")})):
                problems.append(where + ": privileged base-context job reads workflow_run payload")
            if any(str(st.get("uses", "")).strip().startswith(("./", ".\\", "$/"))
                   for st in steps if isinstance(st, dict)):
                problems.append(where + ": privileged base-context/callee local action forbidden")
            if privileged and call and not any(re.fullmatch(
                    r"""github\.event_name\s*==\s*(['"])workflow_dispatch\1""", p)
                    for p in (condition_terms(cond) or [])):
                problems.append(where + ": privileged reusable callee requires positive workflow_dispatch guard")
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
    if directory.parent.is_symlink() or directory.is_symlink() or not directory.is_dir():
        return ["No workflows found"]
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in (".yml", ".yaml"))
    if not paths:
        return ["No workflows found"]
    problems = []
    workflows = {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            problems.append(name + ": workflow must be a regular file")
            continue
        try:
            data = path.read_bytes()
            workflows[name] = (strict_load(yaml, data.decode("utf-8")),
                               hashlib.sha256(data).hexdigest())
        except yaml.YAMLError:
            problems.append(name + ": invalid or unsupported YAML")
        except Exception as error:  # unanalyzable input is a denial, never a pass
            problems.append(name + ": audit failed closed (" + type(error).__name__ + ")")
    exact_callee = workflows.get(PREVIEW_CALLEE, (None, None))[1] == PREVIEW_CALLEE_BYTES
    for name, (doc, digest) in workflows.items():
        exact_caller = name == GUARDED_AZURE and exact_callee and digest == PREVIEW_CALLER_BYTES
        if name == GUARDED_AZURE and exact_callee and not exact_caller:
            problems.append(name + ": staged preview callee requires the exact reviewed caller or no caller")
        try:
            problems.extend(_examine(
                name, doc,
                exact_callee=name == PREVIEW_CALLEE and exact_callee,
                exact_caller=exact_caller,
            ))
        except Exception as error:
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
