#!/usr/bin/env python3
"""Offline validation of the NON-BILLABLE worker IaC safety contract.

Bicep source-only checks; no Azure RBAC or deployed identity proof.
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
        "param identityName string = 'id-textvideo-worker-dev'",
        "secrets: []",
        "triggerType: 'Manual'",
        "'worker_runtime.py'",
        "'--check'",
        "name: 'ENABLE_LIVE_VIDEO_RENDERING'",
        "name: 'ENABLE_WORKER_CONSUMPTION'",
        "name: 'VIDEO_RENDER_PROVIDER'",
    ]
    for marker in required:
        assert marker in text, f"Missing safe configuration: {marker}"
    forbidden = [
        "falKeyVaultSecretUri", "fal-api-key", "name: 'FAL_KEY'",
        "secretRef: 'fal-api-key'", "id-textvideo-api-dev",
    ]
    for marker in forbidden:
        assert marker not in text, f"Unsafe billable configuration: {marker}"
    assert "value: 'disabled'" in text
    assert "value: 'false'" in text
    assert intent["trigger_type"] == "Manual"
    assert intent["live_rendering_enabled"] is False
    assert intent["queue_consumption_enabled"] is False
    assert intent["provider_credentials_configured"] is False
    assert intent["existing_managed_identity"] == "id-textvideo-worker-dev"
    assert intent["deployment_approved"] is False
    return {
        "valid": True,
        "secret_provider": None,
        "credential_stored_in_source": False,
        "credential_configured_in_worker": False,
        "worker_identity": intent["existing_managed_identity"],
        "azure_rbac_verified": False,
        "deployment_approved": False,
        "worker_mode": "inspection_only",
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
