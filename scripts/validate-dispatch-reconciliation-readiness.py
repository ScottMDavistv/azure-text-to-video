#!/usr/bin/env python3
"""Compiled-ARM, fail-closed PR #28 release-intent checks. No Azure calls or writes.

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



def _require(condition, description):
    if not condition:
        raise ReadinessError("Unsafe compiled ARM: " + description)


def _compile(source, filename):
    """Compile the actual candidate, never inspect Bicep text for safeguards."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="dispatch-bicep-") as directory:
        path = Path(directory) / filename
        path.write_text(source, encoding="utf-8")
        try:
            completed = subprocess.run(
                ["bicep", "build", str(path), "--stdout"],
                check=False, capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReadinessError("Bicep compiler unavailable or timed out") from exc
        if completed.returncode:
            raise ReadinessError("Bicep compilation failed")
        try:
            document = json.loads(completed.stdout)
        except (ValueError, TypeError) as exc:
            raise ReadinessError("Invalid compiled ARM JSON") from exc
        _require(isinstance(document, dict), "document is not an object")
        return document


def _parameter(document, name, default=None, allowed=None):
    parameters = document.get("parameters", {})
    _require(isinstance(parameters, dict) and name in parameters, "missing parameter " + name)
    parameter = parameters[name]
    _require(isinstance(parameter, dict), "invalid parameter " + name)
    if default is not None:
        _require(parameter.get("defaultValue") == default, "default for " + name)
    if allowed is not None:
        _require(parameter.get("allowedValues") == allowed, "allowed values for " + name)


def _only_resource(document, resource_type, condition):
    resources = document.get("resources")
    _require(isinstance(resources, list) and len(resources) == 1,
             "expected exactly one conditional resource; additional resources prohibited")
    resource = resources[0]
    _require(isinstance(resource, dict) and resource.get("type") == resource_type,
             "unexpected resource type, including IAM grants")
    _require(resource.get("condition") == condition, "unconditional or bypassed activation")
    return resource


def _object(value, description):
    _require(isinstance(value, dict), description + " must be an object")
    return value


def _array(value, count, description):
    _require(isinstance(value, list) and len(value) == count, description + " count")
    return value


def _job_policy(arm):
    _parameter(arm, "activateReconciliation", False)
    _parameter(arm, "cronExpression", "0 * * * *",
               ["*/15 * * * *", "0 * * * *"])
    resource = _only_resource(arm, "Microsoft.App/jobs",
                              "[parameters('activateReconciliation')]")
    props = _object(resource.get("properties"), "job properties")
    cfg = _object(props.get("configuration"), "job configuration")
    for key, value in {"triggerType": "Schedule", "replicaTimeout": 300,
                       "replicaRetryLimit": 0}.items():
        _require(cfg.get(key) == value, "job configuration " + key)
    _require(cfg.get("secrets") == [], "secrets must be empty")
    schedule = _object(cfg.get("scheduleTriggerConfig"), "schedule")
    for key, value in {
        "cronExpression": "[parameters('cronExpression')]",
        "parallelism": 1, "replicaCompletionCount": 1,
    }.items():
        _require(schedule.get(key) == value, "schedule " + key)
    containers = _array(_object(props.get("template"), "job template").get("containers"),
                        1, "containers")
    container = _object(containers[0], "container")
    _require(container.get("command") == ["python", "-m", "dispatch_reconcile_cli"],
             "reconciler entry point")
    resources = _object(container.get("resources"), "container resources")
    _require(resources.get("cpu") in (0.5, "[json('0.5')]") and resources.get("memory") == "1Gi",
             "container cost bounds")
    expected = {
        "AZURE_CLIENT_ID": "[reference(resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', parameters('reconciliationIdentityName')), '2023-01-31').clientId]",
        "AZURE_STORAGE_ACCOUNT": "[parameters('storageAccountName')]",
        "AZURE_JOBS_QUEUE": "[parameters('queueName')]",
        "AZURE_JOBS_TABLE": "[parameters('jobsTableName')]",
        "VIDEO_RENDER_PROVIDER": "disabled",
        "ENABLE_PAID_FAL_WORKER": "false",
        "ENABLE_LIVE_VIDEO_RENDERING": "false",
        "ENABLE_WORKER_CONSUMPTION": "false",
        "ENABLE_PROJECT_ROUTES": "false",
    }
    environment = _array(container.get("env"), 10, "environment entries")
    actual = {}
    for entry in environment:
        _require(isinstance(entry, dict) and set(entry) == {"name", "value"},
                 "environment must use literal values, not secretRef")
        name = entry["name"]
        _require(isinstance(name, str) and name not in actual, "duplicate/invalid env name")
        _require(not name.upper().startswith("FAL"), "FAL credentials forbidden")
        actual[name] = entry["value"]
    _require(set(actual) == set(expected) | {"ENABLE_DISPATCH_RECONCILIATION"},
             "unexpected or missing environment name")
    for name, value in expected.items():
        if name == "AZURE_CLIENT_ID":
            _require(isinstance(actual[name], str)
                     and "Microsoft.ManagedIdentity/userAssignedIdentities" in actual[name]
                     and "clientId" in actual[name], "managed identity client ID expression")
        else:
            _require(actual[name] == value, "environment value " + name)
    _require(actual["ENABLE_DISPATCH_RECONCILIATION"] ==
             "[if(parameters('activateReconciliation'), 'true', 'false')]",
             "reconciliation activation env value")
    serialized = json.dumps(container)
    _require("secretRef" not in serialized and "FAL_KEY" not in serialized
             and "FAL_API_KEY" not in serialized, "credential injection")


def _alert_policy(arm):
    _parameter(arm, "activateAlerts", False)
    resource = _only_resource(arm, "Microsoft.Insights/scheduledQueryRules",
                              "[parameters('activateAlerts')]")
    _require(resource.get("kind") == "LogAlert", "alert kind")
    props = _object(resource.get("properties"), "alert properties")
    for key, value in {
        "enabled": True, "severity": 2,
        "evaluationFrequency": "PT15M", "windowSize": "PT30M",
    }.items():
        _require(props.get(key) == value, "alert property " + key)
    actions = _object(props.get("actions"), "alert actions")
    _require(actions.get("actionGroups") ==
             ["[parameters('actionGroupResourceId')]"],
             "missing Action Group target")
    criteria = _object(props.get("criteria"), "alert criteria")
    query = _object(_array(criteria.get("allOf"), 1, "alert criteria")[0],
                    "alert query").get("query")
    _require(isinstance(query, str)
             and "ContainerAppConsoleLogs_CL" in query
             and "Log_s contains" in query
             and "dispatch_reconciliation_requires_attention" in query,
             "anomaly-marker query")


def validate_sources(job, alert):
    _job_policy(_compile(job, "dispatch-reconcile-job.bicep"))
    _alert_policy(_compile(alert, "dispatch-recovery-alerts.bicep"))
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
