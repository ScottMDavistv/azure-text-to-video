#!/usr/bin/env python3
"""Offline fail-closed static guard for Azure worker deployment governance."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
workflow = (root / ".github/workflows/guarded-azure-worker-deploy.yml").read_text()
legacy = (root / ".github/workflows/deploy-infrastructure.yml").read_text()

required = [
    "branches: [main]",
    "environment: dev-deployment",
    "environment: dev-deployment-apply",
    "client-id: ${{ vars.AZURE_PREVIEW_CLIENT_ID }}",
    "client-id: ${{ vars.AZURE_DEPLOY_CLIENT_ID }}",
    "github.actor == 'ScottMDavistv'",
    "inputs.approved_sha == github.sha",
    'test "$APPROVED_SHA" = "$GITHUB_SHA"',
    "inputs.confirmation == 'DEPLOY-DEV-WORKER'",
    "vars.AZURE_DEV_DEPLOY_ENABLED == 'true'",
    "vars.PROTECTED_MAIN_VERIFIED == 'true'",
    "python scripts/verify_azure_worker_safe.py",
    "--template-file infra/worker-job.bicep",
    "python scripts/validate-fal-keyvault-iac.py",
]
missing = [guard for guard in required if guard not in workflow]
assert not missing, f"Missing protected worker deployment safeguards: {missing}"

jobs = workflow.split("\njobs:", 1)[1]
preview = jobs.split("\n  preview:", 1)[1].split("\n  whatif:", 1)[0]
whatif = jobs.split("\n  whatif:", 1)[1].split("\n  deploy:", 1)[0]
deploy = jobs.split("\n  deploy:", 1)[1]
assert "az deployment group create" not in preview
assert "az deployment group what-if" not in preview
assert "AZURE_DEPLOY_CLIENT_ID" not in preview
assert "az deployment group what-if" in whatif
assert "AZURE_DEPLOY_CLIENT_ID" in whatif
assert "az deployment group create" in deploy
for step in (whatif, deploy):
    assert "--result-format FullResourcePayloads --output json" in step
    assert "python scripts/verify_worker_image_whatif.py" in step
    assert "umask 077" in step and "trap 'rm -f" in step
assert deploy.index("python scripts/verify_worker_image_whatif.py") < deploy.index("az deployment group create")
assert "AZURE_DEPLOY_CLIENT_ID" in deploy
assert "AZURE_CLIENT_ID" not in deploy
assert "az deployment group create" not in legacy, "Legacy broad deployment must remain disabled"
assert "infra/main.bicep" not in workflow, "Broad foundation deployment is forbidden"
print("Azure OIDC deployment governance static checks passed (no Azure calls).")
