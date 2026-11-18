#!/usr/bin/env python3
"""Strictly isolated Azure what-if evidence collector, no deploy/IAM/provider actions.

Runs only on protected main under the existing audit OIDC environment. Fails
closed for unexpected changes. Raw Azure output is NEVER printed or uploaded.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUBSCRIPTION = "41fbffdc-1793-44f9-b70f-220ea8a9bc6c"
RG = "rg-textvideo-dev"
JOB = "job-textvideo-worker-dev"
REGISTRY = "acrtextvideo1794671698.azurecr.io"
TAG = "692bb1b7f7bd467f1d64e7288115424d21506c12"
DIGEST = "sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646"
IMAGE = REGISTRY + "/textvideo-worker@" + DIGEST
EXISTING_IMAGE = REGISTRY + "/textvideo-worker:" + TAG
REVIEWED_SHA = "e5e1bcbd0a9cb270957febb71faf884d8fc0b649"
JOB_ID = ("/subscriptions/" + SUBSCRIPTION + "/resourceGroups/" + RG +
          "/providers/Microsoft.App/jobs/" + JOB)
BICEP = Path("reviewed/infra/worker-job.bicep")
EVIDENCE = Path("azure-worker-whatif-evidence.json")
SUMMARY = Path("azure-worker-whatif-evidence.md")


class Blocked(Exception):
    """Fixed diagnostic stage; never include Azure stderr/stdout in messages."""
    def __init__(self, message, *, stage='preflight'):
        super().__init__(message)
        self.stage = stage
        self.error_category = 'not-classified'

SAFE_STAGES = frozenset({
    'preflight', 'template-validation', 'account-inspection',
    'worker-resource-read', 'worker-baseline-check',
    'arm-whatif-preview', 'image-only-diff-classification',
})



# Classify Azure's *structured error code*, not arbitrary message text.
# No part of stderr/stdout is included in reports. Unrecognized or conflicting
# codes are always 'unknown'; in particular do not infer IAM from a generic
# nonzero exit code or from a code merely mentioned in prose.
AZURE_ERROR_CODES = {
    'AuthorizationFailed': 'rbac-denied',
    'LinkedAuthorizationFailed': 'rbac-denied',
    'InvalidTemplate': 'template-validation',
    'InvalidTemplateDeployment': 'template-validation',
    'InvalidDeploymentParameter': 'template-validation',
    'BicepError': 'template-validation',
}
SAFE_ERROR_CATEGORIES = frozenset({
    'not-classified', 'rbac-denied', 'template-validation',
    'cli-unavailable', 'unknown',
})
# Recognize the Azure CLI's leading error token, or a JSON top-level error.code
# envelope only. Never search narrative message strings or nested arbitrary JSON.
AZURE_CLI_CODE = re.compile(r'^\s*(?:ERROR:\s*)?\(([A-Za-z][A-Za-z0-9]*)\)', re.ASCII)


def classify_azure_failure(stderr):
    if not isinstance(stderr, str) or len(stderr) > 65536:
        return 'unknown'
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        return 'unknown'
    match = AZURE_CLI_CODE.match(lines[0])
    if match:
        # A second leading CLI error token is ambiguous even if it repeats the
        # first code. Only inspect line prefixes, never narrative substrings.
        if any(AZURE_CLI_CODE.match(line) for line in lines[1:]):
            return 'unknown'
        return AZURE_ERROR_CODES.get(match.group(1), 'unknown')
    try:
        payload = json.loads(stderr)
    except (TypeError, ValueError):
        return 'unknown'
    if not isinstance(payload, dict) or not isinstance(payload.get('error'), dict):
        return 'unknown'
    code = payload['error'].get('code')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', code):
        return 'unknown'
    return AZURE_ERROR_CODES.get(code, 'unknown')


def blocked_at(message, *, stage, category='not-classified'):
    if category not in SAFE_ERROR_CATEGORIES:
        category = 'unknown'
    error = Blocked(message, stage=stage)
    error.error_category = category
    return error

def preflight_env(env):
    if (env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("AZURE_AUDIT_OPERATIONS_ENABLED") != "true"
            or env.get("GITHUB_REPOSITORY") != "ScottMDavistv/azure-text-to-video"
            or env.get("GITHUB_REPOSITORY_ID") != "1457187422"
            or env.get("GITHUB_REPOSITORY_OWNER_ID") != "212811615"):
        raise Blocked("trusted main manual audit context not established")
    if env.get("AZURE_SUBSCRIPTION_ID") != SUBSCRIPTION:
        raise Blocked("wrong subscription configuration")


def validate_template(source):
    """Reject drift from reviewed safe, single-resource template."""
    required = (
        "param workerImage string", "@allowed([", "'" + IMAGE + "'",
        "resource worker 'Microsoft.App/jobs@2024-03-01'",
        "workloadProfileName: 'Consumption'", "triggerType: 'Manual'",
        "replicaRetryLimit: 0", "secrets: []",
        "'worker_runtime.py'", "'--check'",
        "name: 'VIDEO_RENDER_PROVIDER'", "value: 'disabled'",
        "name: 'ENABLE_WORKER_CONSUMPTION'",
        "name: 'ENABLE_LIVE_VIDEO_RENDERING'", "value: 'false'",
    )
    if not all(item in source for item in required):
        raise Blocked("reviewed worker template safety gate failed")
    # Preclude additional managed resources in the isolated worker template.
    declarations = re.findall(r"\bresource\s+\w+\s+'([^']+)'\s*=\s*\{", source)
    if declarations != ["Microsoft.App/jobs@2024-03-01"]:
        raise Blocked("unexpected managed resource declarations")


def run_json(argv, *, runner=subprocess.run, timeout=90, stage='preflight'):
    if stage not in SAFE_STAGES:
        raise Blocked('Unknown diagnostic stage')
    # No shell and no raw Azure output on failure.
    try:
        result = runner(argv, capture_output=True, text=True,
                        check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise blocked_at("Azure CLI read or preview unavailable", stage=stage,
                         category="cli-unavailable") from None
    if result.returncode != 0:
        category = classify_azure_failure(getattr(result, "stderr", None))
        raise blocked_at("Azure operation unavailable or not permitted by current identity",
                         stage=stage, category=category) from None
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Blocked("Azure response was not JSON", stage=stage) from None
    if not isinstance(value, dict):
        raise Blocked("Azure response JSON shape unexpected", stage=stage)
    return value


def check_existing(job):
    if job.get("id", "").casefold() != JOB_ID.casefold():
        raise Blocked("wrong worker resource")
    props = job.get("properties") or {}
    config = props.get("configuration") or {}
    template = props.get("template") or {}
    containers = template.get("containers") or []
    if (len(containers) != 1
            or containers[0].get("image") != EXISTING_IMAGE
            or containers[0].get("command") != ["python"]
            or containers[0].get("args") != ["worker_runtime.py", "--check"]
            or config.get("triggerType") != "Manual"
            or props.get("workloadProfileName") != "Consumption"
            or config.get("secrets")
            or len((job.get("identity") or {}).get("userAssignedIdentities") or {}) != 1):
        raise Blocked("existing Azure worker is not the expected disabled baseline")
    env = {x.get("name"): x for x in containers[0].get("env") or []
           if isinstance(x, dict)}
    for key, expected in (("VIDEO_RENDER_PROVIDER", "disabled"),
                          ("ENABLE_WORKER_CONSUMPTION", "false"),
                          ("ENABLE_LIVE_VIDEO_RENDERING", "false")):
        if env.get(key, {}).get("value") != expected or env.get(key, {}).get("secretRef"):
            raise Blocked("existing Azure worker provider/consumption safeguards differ")
    return True


def classify_whatif(payload):
    """Pass only one exact image delta on the existing job. All else blocks."""
    changes = payload.get("changes")
    if not isinstance(changes, list):
        raise Blocked("Azure what-if response did not contain change list")
    modifications = []
    for change in changes:
        if not isinstance(change, dict):
            raise Blocked("unexpected what-if change shape")
        kind = change.get("changeType")
        if kind in ("NoChange", "Ignore"):
            continue
        if kind != "Modify" or str(change.get("resourceId", "")).casefold() != JOB_ID.casefold():
            raise Blocked("preview contains non-image change or other resource")
        modifications.append(change)
    if len(modifications) != 1:
        raise Blocked("preview must contain exactly one worker image modification")
    delta = modifications[0].get("delta")
    if not isinstance(delta, list) or len(delta) != 1:
        raise Blocked("preview did not isolate one image property change")
    item = delta[0]
    if not isinstance(item, dict):
        raise Blocked("unexpected Azure what-if delta shape")
    path = str(item.get("path", "")).removeprefix("$.").removeprefix(".")
    if (path != "properties.template.containers[0].image"
            or item.get("propertyChangeType") != "Modify"
            or item.get("before") != EXISTING_IMAGE
            or item.get("after") != IMAGE):
        raise Blocked("preview is not the approved image-only tag-to-digest change")
    return {"resource": "job-textvideo-worker-dev",
            "change_type": "Modify",
            "changed_field": "properties.template.containers[0].image",
            "old_image_type": "mutable-git-sha-tag",
            "new_image_digest": DIGEST,
            "image_only": True}


def execute(*, runner=subprocess.run, environment=os.environ, template_path=BICEP):
    preflight_env(environment)
    source = template_path.read_text(encoding="utf-8")
    try:
        validate_template(source)
    except Blocked as exc:
        raise Blocked(str(exc), stage='template-validation') from None
    account = run_json(["az", "account", "show", "--output", "json"], runner=runner, stage="account-inspection")
    if account.get("id", "").lower() != SUBSCRIPTION:
        raise Blocked("Azure account is not on approved dev subscription", stage="account-inspection")
    job = run_json(["az", "resource", "show", "--ids", JOB_ID,
                    "--api-version", "2024-03-01", "--output", "json"], runner=runner, stage="worker-resource-read")
    try:
        check_existing(job)
    except Blocked as exc:
        raise Blocked(str(exc), stage='worker-baseline-check') from None
    # The only ARM preview operation. It may require whatIf/action beyond Reader;
    # absence of permission MUST NOT result in an IAM grant or another identity.
    preview = run_json([
        "az", "deployment", "group", "what-if",
        "--resource-group", RG,
        "--template-file", str(template_path),
        "--parameters", "workerImage=" + IMAGE,
        "--result-format", "FullResourcePayloads",
        "--output", "json",
    ], runner=runner, timeout=120, stage="arm-whatif-preview")
    try:
        return classify_whatif(preview)
    except Blocked as exc:
        raise Blocked(str(exc), stage='image-only-diff-classification') from None


def report(result=None, blocked=None, stage=None, error_category="not-classified"):
    if stage is not None and stage not in SAFE_STAGES:
        raise ValueError('Unexpected diagnostic stage')
    if error_category not in SAFE_ERROR_CATEGORIES:
        raise ValueError('Unexpected error category')
    if error_category == "rbac-denied" and stage != "arm-whatif-preview":
        raise ValueError("RBAC category is allowed only for the exact ARM preview")
    return {
        "schema_version": 1, "operation": "Azure ARM group what-if only",
        "subscription_scope": "approved development subscription",
        "resource_group": RG, "worker": JOB,
        "reviewed_template_commit": REVIEWED_SHA,
        "image_digest": DIGEST,
        "state": "image-only-preview" if result else "blocked-unverified",
        "deployment_performed": False, "iam_modified": False,
        "worker_started": False, "paid_provider_called": False,
        "finding": result if result else None,
        "blocker": blocked if not result else None,
        "diagnostic_stage": "image-only-diff-classification" if result else (stage or "preflight"),
        "arm_error_category": (error_category if not result else "not-classified"),
        "authorization": "what-if only; separate merge and deploy approvals required",
    }


def write_report(data):
    EVIDENCE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    lines = ["# Isolated Azure worker what-if", "",
             "**Result:** " + data["state"],
             "**Worker:** " + JOB,
             "**Reviewed template SHA:** `" + REVIEWED_SHA + "`",
             "**Manifest digest:** `" + DIGEST + "`",
             "**Deployment or IAM changes:** None", ""]
    if data["finding"]:
        lines += ["**Permitted image-only delta was verified.**"]
    else:
        lines += ["**Blocked stage:** `" + data["diagnostic_stage"] + "`",
                  "**Allowlisted ARM error category:** `" + data["arm_error_category"] + "`",
                  "**Blocked:** " + (data["blocker"] or "unverified")]
    lines += ["", "This report is NOT deployment or merge authorization."]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    try:
        result = execute()
        data = report(result)
    except Exception as exc:
        # Only our fixed, non-sensitive reasons are reportable. Never expose raw
        # CLI stderr, ARM preview JSON, environment values or tokens.
        reason = str(exc) if isinstance(exc, Blocked) else "preflight or Azure operation failed"
        stage = exc.stage if isinstance(exc, Blocked) else 'preflight'
        category = exc.error_category if isinstance(exc, Blocked) else "unknown"
        if stage != "arm-whatif-preview":
            category = "not-classified"
        data = report(blocked=reason, stage=stage, error_category=category)
    write_report(data)
    print("Azure isolated what-if:", data["state"])
    return 0 if data["finding"] else 2


if __name__ == "__main__":
    sys.exit(main())
