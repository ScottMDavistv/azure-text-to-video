#!/usr/bin/env python3
"""Azure dev worker read-only evidence collector (allowlisted ARM/CLI reads only).

No Azure writes, secrets, deployments, container executions, or provider SDKs.
Output intentionally excludes identity IDs, raw Azure responses, and env secrets.
"""
import datetime as dt
import json
import os
import pathlib
import re
import subprocess

RG = "rg-textvideo-dev"
JOB = "job-textvideo-worker-dev"
WORKER_IDENTITY = "id-textvideo-worker-dev"
AUDIT_IDENTITY = "id-textvideo-github-audit-dev"
DEPLOY_IDENTITY = "id-textvideo-github-deploy-dev"
ACR = "acrtextvideo1794671698"
PREVIEW_CLIENT_ID = "82b41ada-d973-4ed4-8b13-7de4458185dd"
EXPECTED_SUBSCRIPTION = "41fbffdc-1793-44f9-b70f-220ea8a9bc6c"
FLAGS = {
    "VIDEO_RENDER_PROVIDER": "disabled",
    "ENABLE_WORKER_CONSUMPTION": "false",
    "ENABLE_LIVE_VIDEO_RENDERING": "false",
    "ENABLE_PAID_FAL_WORKER": "false",
}
RESULT_FILE = pathlib.Path("azure-readonly-operations.json")
SUMMARY_FILE = pathlib.Path("azure-readonly-operations.md")


class AuditError(Exception):
    pass


def run_read(command, *, runner=subprocess.run):
    """Pass argv directly; never surface raw Azure stdout/stderr on failure."""
    if command[:2] not in (
        ["az", "account"], ["az", "resource"], ["az", "identity"],
        ["az", "acr"], ["az", "role"],
    ):
        raise AuditError("Non-allowlisted Azure command")
    if any(part in command for part in (
        "create", "update", "delete", "assign", "start", "exec",
        "deployment", "secret", "credential",
    )):
        raise AuditError("Mutating or credential-related command forbidden")
    try:
        result = runner(command + ["--output", "json"], capture_output=True,
                        text=True, check=True, timeout=90)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, OSError) as exc:
        raise AuditError("Azure read failed: " + type(exc).__name__) from None


def safe_resource_id(value, *, suffix):
    if not isinstance(value, str) or not value.lower().endswith(suffix.lower()):
        raise AuditError("Unexpected Azure resource identity")
    if not value.lower().startswith(
        "/subscriptions/" + EXPECTED_SUBSCRIPTION.lower() + "/resourcegroups/" + RG.lower() + "/"
    ):
        raise AuditError("Resource outside approved development scope")
    return value


def evaluate(job, registry, worker_identity, audit_identity, deploy_identity,
             audit_roles, deploy_roles):
    """Deterministic evaluation of mocked or live read-only Azure responses."""
    findings = []
    def add(name, ok, detail):
        findings.append({"check": name, "status": "pass" if ok else "block",
                         "detail": detail})
    worker_id = safe_resource_id(worker_identity.get("id"), suffix="/userAssignedIdentities/" + WORKER_IDENTITY)
    expected_job_id = safe_resource_id(job.get("id"), suffix="/jobs/" + JOB)
    safe_resource_id(registry.get("id"), suffix="/registries/" + ACR)
    safe_resource_id(audit_identity.get("id"), suffix="/userAssignedIdentities/" + AUDIT_IDENTITY)
    safe_resource_id(deploy_identity.get("id"), suffix="/userAssignedIdentities/" + DEPLOY_IDENTITY)
    add("worker scope", bool(expected_job_id), "Existing development Container Apps job")
    identities = (job.get("identity") or {}).get("userAssignedIdentities") or {}
    add("worker managed identity", len(identities) == 1 and worker_id in identities
        and (job.get("identity") or {}).get("type") == "UserAssigned",
        "Dedicated user-assigned identity only")
    props = job.get("properties") or {}
    conf = props.get("configuration") or {}
    template = props.get("template") or {}
    containers = template.get("containers") or []
    regs = conf.get("registries") or []
    login_server = registry.get("loginServer") or (registry.get("properties") or {}).get("loginServer")
    add("registry identity", len(regs) == 1 and regs[0].get("identity") == worker_id
        and regs[0].get("server") == login_server
        and login_server == ACR + ".azurecr.io",
        "Registry uses dedicated worker identity")
    add("manual worker only", conf.get("triggerType") == "Manual"
        and props.get("workloadProfileName") == "Consumption"
        and len(containers) == 1
        and containers[0].get("command") == ["python"]
        and containers[0].get("args") == ["worker_runtime.py", "--check"],
        "Manual inspection-only worker with Consumption profile")
    image = containers[0].get("image", "") if len(containers) == 1 else ""
    prefix = ACR + ".azurecr.io/"
    is_digest = (isinstance(image, str) and image.startswith(prefix)
                 and bool(re.fullmatch(r"[a-zA-Z0-9_./-]+@sha256:[0-9a-f]{64}",
                                       image[len(prefix):])))
    add("immutable worker image", is_digest,
        "SHA-256 digest pinned" if is_digest else "Image tag or invalid reference; digest required")
    env_items = containers[0].get("env", []) if len(containers) == 1 else []
    env = {x.get("name"): x for x in env_items if isinstance(x, dict)}
    flags_safe = all(
        (name not in env and name == "ENABLE_PAID_FAL_WORKER") or
        (name in env and env[name].get("value", "").lower() == expected
         and not env[name].get("secretRef"))
        for name, expected in FLAGS.items()
    )
    add("provider and worker flags", flags_safe, "Required render/consume flags disabled")
    forbidden_secrets = conf.get("secrets") or []
    add("worker secret references", not forbidden_secrets and
        not any(isinstance(v, dict) and v.get("secretRef") for v in env_items),
        "No secrets or environment secret references")
    add("distinct GitHub identities",
        audit_identity.get("clientId") == PREVIEW_CLIENT_ID and
        bool(deploy_identity.get("clientId")) and
        deploy_identity.get("clientId") != PREVIEW_CLIENT_ID and
        audit_identity.get("principalId") != deploy_identity.get("principalId"),
        "Preview and deployment identities differ")
    def role_names(items):
        if not isinstance(items, list):
            raise AuditError("Azure RBAC response was not a list")
        return sorted({str(x.get("roleDefinitionName", "")) for x in items})
    preview = role_names(audit_roles)
    deploy = role_names(deploy_roles)
    add("preview resource-group RBAC", preview == ["Reader"],
        "Preview identity must be Reader only (RG + inherited)")
    add("deploy resource-group RBAC", not deploy,
        "Deployment identity must have no RG or inherited roles before authorization")
    return {"schema_version": 1,
            "scope": "rg-textvideo-dev (current subscription only)",
            "read_only": True,
            "deployment_performed": False,
            "provider_called": False,
            "findings": findings,
            "deployment_readiness": "blocked" if any(
                f["status"] == "block" for f in findings) else "preflight-only",
            "not_verified": [
                "Live GitHub environment protection and successful OIDC token exchange",
                "Child-resource-specific RBAC and other subscriptions/tenants",
                "ACR digest existence, signature, provenance and pull entitlement",
                "Azure ARM what-if, alert delivery, job executions and runtime health",
            ]}


def collect(*, reader=run_read):
    account = reader(["az", "account", "show"])
    if account.get("id", "").lower() != EXPECTED_SUBSCRIPTION:
        raise AuditError("Wrong Azure subscription; refusing resource inspection")
    scope = "/subscriptions/" + EXPECTED_SUBSCRIPTION + "/resourceGroups/" + RG
    def identity(name):
        return reader(["az", "identity", "show", "--resource-group", RG, "--name", name])
    audit_identity = identity(AUDIT_IDENTITY)
    deploy_identity = identity(DEPLOY_IDENTITY)
    worker_identity = identity(WORKER_IDENTITY)
    job_id = scope + "/providers/Microsoft.App/jobs/" + JOB
    job = reader(["az", "resource", "show", "--ids", job_id,
                  "--api-version", "2024-03-01"])
    registry = reader(["az", "acr", "show", "--resource-group", RG, "--name", ACR])
    def roles(principal):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", str(principal or "")):
            raise AuditError("Invalid identity principal ID")
        return reader(["az", "role", "assignment", "list",
                       "--assignee-object-id", principal,
                       "--scope", scope, "--include-inherited"])
    return evaluate(job, registry, worker_identity, audit_identity, deploy_identity,
                    roles(audit_identity.get("principalId")),
                    roles(deploy_identity.get("principalId")))


def write_report(report):
    RESULT_FILE.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Azure Read-Only Operations — development preflight",
             "", "No Azure changes, job executions, provider calls or credentials retrieved.",
             "", "| Check | Status | Evidence |", "|---|---|---|"]
    for f in report["findings"]:
        lines.append("| " + f["check"] + " | " + f["status"].upper() +
                     " | " + f["detail"] + " |")
    lines += ["", "**Deployment readiness:** " + report["deployment_readiness"],
              "", "### Not verified"]
    lines += ["- " + item for item in report["not_verified"]]
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise SystemExit("Only trusted main-branch automation may access Azure")
    if os.environ.get("AZURE_PREVIEW_CLIENT_ID") != PREVIEW_CLIENT_ID:
        raise SystemExit("Unexpected preview client ID")
    try:
        report = collect()
        write_report(report)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as output:
                output.write(SUMMARY_FILE.read_text(encoding="utf-8"))
        print("Read-only Azure preflight complete; readiness:", report["deployment_readiness"])
    except AuditError as exc:
        # Never print raw CLI output, env values, or identity attributes.
        print("Read-only preflight failed:", str(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
