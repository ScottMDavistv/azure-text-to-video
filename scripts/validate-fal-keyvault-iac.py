#!/usr/bin/env python3

"""
Offline validation of the Fal Key Vault migration.

These checks are guardrails, not a replacement for
Azure Bicep compilation or a deployment what-if.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = ROOT / "infra/worker-job.bicep"

INTENT = ROOT / "infra/worker-job.intent.json"


def validate():
    text = TEMPLATE.read_text()
    intent = json.loads(INTENT.read_text())

    required = [
        "param falKeyVaultSecretUri string",
        "secrets: [",
        "name: 'fal-api-key'",
        "keyVaultUrl: falKeyVaultSecretUri",
        "identity: identity.id",
        "name: 'FAL_KEY'",
        "secretRef: 'fal-api-key'",
        "triggerType: 'Manual'",
        "'worker_runtime.py'",
        "'--check'",
        "name: 'ENABLE_LIVE_VIDEO_RENDERING'",
        "name: 'ENABLE_WORKER_CONSUMPTION'",
    ]

    for item in required:
        assert item in text, (
            f"Missing required configuration: {item}"
        )

    assert text.count(
        "keyVaultUrl: falKeyVaultSecretUri"
    ) == 1

    assert text.count(
        "secretRef: 'fal-api-key'"
    ) == 1

    assert "value: 'disabled'" in text
    assert "value: 'false'" in text

    assert intent["trigger_type"] == "Manual"
    assert intent["live_rendering_enabled"] is False
    assert intent["queue_consumption_enabled"] is False
    assert intent["deployment_approved"] is False

    assert (
        "resource worker 'Microsoft.App/jobs@"
        "2024-03-01'"
    ) in text

    return {
        "valid": True,
        "secret_provider": "Azure Key Vault",
        "credential_stored_in_source": False,
        "deployment_approved": False,
        "worker_mode": "inspection_only",
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
