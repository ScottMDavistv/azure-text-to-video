#!/usr/bin/env python3
"""
Local deployment-intent checks.

This is not a substitute for Bicep compilation,
Azure what-if, RBAC review, or cloud validation.
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BICEP_PATH = ROOT / "infra/worker-job.bicep"
INTENT_PATH = ROOT / "infra/worker-job.intent.json"


class WorkerInfrastructureError(ValueError):
    pass


def validate_worker_infrastructure():
    bicep = BICEP_PATH.read_text()
    intent = json.loads(INTENT_PATH.read_text())

    expected_resource = (
        "resource worker 'Microsoft.App/jobs@2024-03-01'"
    )

    if expected_resource not in bicep:
        raise WorkerInfrastructureError(
            "Missing isolated worker resource"
        )

    if intent["resource_name"] != "job-textvideo-worker-dev":
        raise WorkerInfrastructureError(
            "Unexpected worker job name"
        )

    if intent["resource_group"] != "rg-textvideo-dev":
        raise WorkerInfrastructureError(
            "Unexpected resource group"
        )

    if intent["trigger_type"] != "Manual":
        raise WorkerInfrastructureError(
            "Worker trigger must remain manual"
        )

    if intent["execution_command"] != [
        "python",
        "worker_runtime.py",
        "--check",
    ]:
        raise WorkerInfrastructureError(
            "Worker must start in inspection mode"
        )

    for flag in (
        "live_rendering_enabled",
        "queue_consumption_enabled",
        "auto_execution_enabled",
        "public_ingress_enabled",
        "deployment_approved",
    ):
        if intent[flag] is not False:
            raise WorkerInfrastructureError(
                f"Unsafe deployment intent: {flag}"
            )

    required_fragments = [
        "triggerType: 'Manual'",
        "replicaRetryLimit: 0",
        "parallelism: 1",
        "replicaCompletionCount: 1",
        "identity: identity.id",
        "'worker_runtime.py'",
        "'--check'",
        "name: 'ENABLE_WORKER_CONSUMPTION'",
        "name: 'ENABLE_LIVE_VIDEO_RENDERING'",
        "name: 'VIDEO_RENDER_PROVIDER'",
        "value: 'disabled'",
        "value: 'false'",
    ]

    for fragment in required_fragments:
        if fragment not in bicep:
            raise WorkerInfrastructureError(
                f"Missing safe configuration: {fragment}"
            )

    # Reject additional managed-resource declarations.
    declarations = re.findall(
        r"\bresource\s+\w+\s+'([^']+)'\s*="
        r"\s*\{",
        bicep,
    )

    managed = [
        declaration
        for declaration in declarations
        if declaration.startswith("Microsoft.App/jobs@")
    ]

    if managed != ["Microsoft.App/jobs@2024-03-01"]:
        raise WorkerInfrastructureError(
            "Unexpected managed job resources"
        )

    # Existing-resource declarations must stay explicit.
    for resource_name in (
        "environment",
        "identity",
        "registry",
    ):
        pattern = (
            rf"resource\s+{resource_name}\s+'[^']+'"
            rf"\s+existing\s*="
        )

        if not re.search(pattern, bicep):
            raise WorkerInfrastructureError(
                f"{resource_name} must be existing"
            )

    if intent["allowed_resource_changes"] != [
        "Create Microsoft.App/jobs/job-textvideo-worker-dev"
    ]:
        raise WorkerInfrastructureError(
            "Unexpected allowed changes"
        )

    return {
        "valid": True,
        "worker_name": intent["resource_name"],
        "mode": "inspection_only",
        "deployment_approved": False,
        "requires_azure_what_if": True,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            validate_worker_infrastructure(),
            indent=2,
        )
    )
