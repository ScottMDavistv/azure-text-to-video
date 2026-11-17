#!/usr/bin/env python3
"""Static, fail-closed PR #28 release-intent checks. No Azure calls or writes.

This is *not* proof that an identity, RBAC assignment, immutable ACR digest,
Log Analytics table, action group or Azure budget exists in the live tenant.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "infra/dispatch-reconcile-job.bicep"
ALERT = ROOT / "infra/dispatch-recovery-alerts.bicep"
REQUIRED_JOB = (
    "param activateReconciliation bool = false",
    "triggerType: 'Schedule'",
    "replicaRetryLimit: 0",
    "parallelism: 1",
    "replicaCompletionCount: 1",
    "replicaTimeout: 300",
    "name: 'ENABLE_DISPATCH_RECONCILIATION'",
    "name: 'ENABLE_PAID_FAL_WORKER'",
    "name: 'ENABLE_LIVE_VIDEO_RENDERING'",
    "name: 'ENABLE_WORKER_CONSUMPTION'",
    "name: 'VIDEO_RENDER_PROVIDER'",
    "value: 'disabled'",
    "secrets: []",
    "identity: identity.id",
    "name: 'AZURE_CLIENT_ID'",
    "command: [ 'python', '-m', 'dispatch_reconcile_cli' ]",
)
REQUIRED_ALERT = (
    "param activateAlerts bool = false",
    "if (activateAlerts)",
    "kind: 'LogAlert'",
    "severity: 2",
    "evaluationFrequency: 'PT15M'",
    "windowSize: 'PT30M'",
    "ContainerAppConsoleLogs_CL",
    "Log_s contains",
    "dispatch_reconciliation_requires_attention",
    "actionGroups: [actionGroupResourceId]",
)
ACR_HOST = "acrtextvideo1794671698.azurecr.io"
IMAGE_RE = re.compile(r"^" + re.escape(ACR_HOST)
                      + r"/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
AG_RE = re.compile(
    r"^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/"
    r"[^/]+/providers/Microsoft\.Insights/actionGroups/[^/]+$",
    re.IGNORECASE,
)


class ReadinessError(ValueError):
    pass


def validate_image(value):
    if not isinstance(value, str) or not IMAGE_RE.fullmatch(value):
        raise ReadinessError("Image must be an immutable digest from the approved ACR")
    return True


def validate_action_group(value):
    if not isinstance(value, str) or not AG_RE.fullmatch(value):
        raise ReadinessError("Missing or invalid Azure Monitor Action Group resource ID")
    return True


def validate_sources(job, alert):
    for item in REQUIRED_JOB:
        if item not in job:
            raise ReadinessError("Missing job safeguard: " + item)
    for item in REQUIRED_ALERT:
        if item not in alert:
            raise ReadinessError("Missing alert safeguard: " + item)
    if not re.search(r"param cronExpression string = '0 \\* \\* \\* \\*'", job):
        raise ReadinessError("Unbounded or changed default cron")
    if "@allowed([ '*/15 * * * *', '0 * * * *' ])" not in job:
        raise ReadinessError("Unrestricted cron frequency")
    for name in ("environment", "identity", "registry"):
        if not re.search(r"resource\s+" + name + r"\s+'[^']+'\s+existing\s*=", job):
            raise ReadinessError("Must reference existing " + name)
    if "resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing" not in alert:
        raise ReadinessError("Log Analytics workspace must be existing")
    if "secretRef" in job or "FAL_KEY" in job or "FAL_API_KEY" in job:
        raise ReadinessError("Provider credentials may not enter reconciliation job")
    if "ENABLE_DISPATCH_RECONCILIATION', value: 'true'" in job:
        raise ReadinessError("Unconditionally activated reconciliation")
    if "activateReconciliation ? [ '--run-once'" not in job:
        raise ReadinessError("No conditional preflight/runtime command")
    if " : [ '--check' ]" not in job:
        raise ReadinessError("Preflight-only default removed")
    if "= if (activateReconciliation) {" not in job:
        raise ReadinessError("Disabled mode must create no scheduled Azure job")
    if "replicaTimeout: 300" not in job or "cpu: json('0.5')" not in job or "memory: '1Gi'" not in job:
        raise ReadinessError("Unexpected unit runtime / resource cost bound")
    return {"static_checks": "pass", "live_azure": "not_verified",
            "image_digest": "requires_approved_parameter",
            "identity_rbac": "requires_readonly_cloud_evidence",
            "log_schema": "requires_live_query",
            "action_group": "requires_delivery_test",
            "cost": "requires_budget_and_actual_metering"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-image", help="Expected image digest to validate, not required for static preflight")
    parser.add_argument("--action-group-id", help="Expected monitored Action Group resource id")
    args = parser.parse_args(argv)
    evidence = validate_sources(JOB.read_text(), ALERT.read_text())
    if args.worker_image is not None:
        validate_image(args.worker_image)
        evidence["image_digest"] = "syntax_valid_only"
    if args.action_group_id is not None:
        validate_action_group(args.action_group_id)
        evidence["action_group"] = "resource_id_syntax_valid_only"
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
