#!/usr/bin/env python3
"""Azure dev worker read-only evidence collector (allowlisted ARM/CLI reads only).

No Azure writes, secrets, deployments, container executions, or provider SDKs.
Output intentionally excludes identity IDs, raw Azure responses, and env secrets.
"""
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
# Validated against environment variables; identifiers must not be hardcoded in sources.
PREVIEW_CLIENT_ID = os.environ.get("AZURE_AUDIT_CLIENT_ID", "")
EXPECTED_SUBSCRIPTION = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
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


def is_uuid(value):
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", str(value or "")))


def allowed_command(command):
    """Strict command-shape allowlist; optional CLI flags cannot widen reads."""
    if not isinstance(command, list):
        return False
    if command == ["az", "account", "show"]:
        return True
    if command[:3] == ["az", "identity", "show"] and len(command) == 7:
        return (command[3:5] == ["--resource-group", RG]
                and command[5] == "--name"
                and command[6] in (AUDIT_IDENTITY, DEPLOY_IDENTITY, WORKER_IDENTITY))
    if command[:3] == ["az", "resource", "show"] and len(command) == 7:
        expected = ("/subscriptions/" + EXPECTED_SUBSCRIPTION +
                    "/resourceGroups/" + RG +
                    "/providers/Microsoft.App/jobs/" + JOB)
        return command[3:5] == ["--ids", expected] and command[5:] == [
            "--api-version", "2024-03-01"]
    if command == ["az", "acr", "show", "--resource-group", RG, "--name", ACR]:
        return True
    if command[:4] == ["az", "role", "assignment", "list"] and len(command) == 9:
        scope = "/subscriptions/" + EXPECTED_SUBSCRIPTION + "/resourceGroups/" + RG
        return (command[4] == "--assignee-object-id" and is_uuid(command[5])
                and command[6:] == ["--scope", scope, "--include-inherited"])
    return False


def run_read(command, *, runner=subprocess.run):
    """Only exact approved commands; never emit Azure CLI stdout/stderr on failure."""
    if not allowed_command(command):
        raise AuditError("Non-allowlisted Azure read operation")
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
    add("worker managed identity", len(identities) == 1 and worker_id.casefold() in {str(k).casefold() for k in identities}
        and (job.get("identity") or {}).get("type") == "UserAssigned",
        "Dedicated user-assigned identity only")
    props = job.get("properties") or {}
    conf = props.get("configuration") or {}
    template = props.get("template") or {}
    containers = template.get("containers") or []
    regs = conf.get("registries") or []
    login_server = registry.get("loginServer") or (registry.get("properties") or {}).get("loginServer")
    add("registry identity", len(regs) == 1 and str(regs[0].get("identity") or "").casefold() == worker_id.casefold()
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
        (name in env and str(env[name].get("value") or "").lower() == expected
         and not env[name].get("secretRef"))
        for name, expected in FLAGS.items()
    )
    add("provider and worker flags", flags_safe, "Required render/consume flags disabled")
    forbidden_secrets = conf.get("secrets") or []
    add("worker secret references", not forbidden_secrets and
        not any(isinstance(v, dict) and v.get("secretRef") for v in env_items),
        "No secrets or environment secret references")
    add("distinct GitHub identities",
        bool(PREVIEW_CLIENT_ID) and audit_identity.get("clientId") == PREVIEW_CLIENT_ID and
        bool(deploy_identity.get("clientId")) and
        deploy_identity.get("clientId") != PREVIEW_CLIENT_ID and
        audit_identity.get("principalId") != deploy_identity.get("principalId"),
        "Preview and deployment identities differ")
    required_scope = ("/subscriptions/" + EXPECTED_SUBSCRIPTION
                      + "/resourceGroups/" + RG).casefold()
    def role_entries(items):
        if not isinstance(items, list):
            raise AuditError("Azure RBAC response was not a list")
        return [(str(x.get("roleDefinitionName", "")),
                 str(x.get("scope", "")).casefold()) for x in items
                if isinstance(x, dict)]
    preview = role_entries(audit_roles)
    deploy = role_entries(deploy_roles)
    add("preview resource-group RBAC",
        len(preview) == 1 and preview[0] == ("Reader", required_scope),
        "Exactly Reader at the development resource group; inherited/subscription grants block")
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
    if not is_uuid(EXPECTED_SUBSCRIPTION) or not is_uuid(PREVIEW_CLIENT_ID):
        raise AuditError("Missing or invalid expected Azure identity configuration")
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
        if not is_uuid(principal):
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
    if not is_uuid(PREVIEW_CLIENT_ID) or not is_uuid(EXPECTED_SUBSCRIPTION):
        raise SystemExit("Dedicated audit identity and subscription UUIDs required")
    try:
        report = collect()
        write_report(report)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as output:
                output.write(SUMMARY_FILE.read_text(encoding="utf-8"))
        print("Read-only Azure preflight complete; readiness:", report["deployment_readiness"])
    except Exception:
        # Fail closed without leaking exception text, raw CLI output, or identity values.
        # A sanitized failure artifact is still published even on unexpected responses.
        failure = {"schema_version": 1, "read_only": True,
                   "deployment_performed": False, "provider_called": False,
                   "deployment_readiness": "blocked",
                   "findings": [{"check": "Azure audit execution", "status": "block",
                                 "detail": "Azure read or response validation failed"}],
                   "not_verified": ["Azure preflight did not complete"]}
        try:
            write_report(failure)
        except OSError:
            pass
        print("Read-only preflight blocked: Azure read or response validation failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
