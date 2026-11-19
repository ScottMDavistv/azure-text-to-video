"""Offline compiled-ARM readiness policy and adversarial regression tests."""
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = spec_from_file_location(
    "dispatch_readiness", ROOT / "scripts/validate-dispatch-reconciliation-readiness.py"
)
module = module_from_spec(spec)
spec.loader.exec_module(module)

JOB = (ROOT / "infra/dispatch-reconcile-job.bicep").read_text()
ALERT = (ROOT / "infra/dispatch-recovery-alerts.bicep").read_text()


def test_existing_templates_fail_closed_but_do_not_claim_live_verification():
    evidence = module.validate_sources(JOB, ALERT)
    assert evidence["static_checks"] == "pass"
    assert evidence["live_azure"] == "not_verified"
    assert evidence["identity_rbac"] == "requires_readonly_cloud_evidence"


def test_immutable_image_requires_sha256_digest_and_approved_acr():
    good = "acrtextvideo1794671698.azurecr.io/textvideo-backend@sha256:" + "a" * 64
    assert module.validate_image(good)
    for value in (
        "acrtextvideo1794671698.azurecr.io/textvideo-backend:latest",
        "docker.io/textvideo-backend@sha256:" + "a" * 64,
        "acrtextvideo1794671698.azurecr.io/textvideo-backend@sha256:deadbeef",
        "",
    ):
        with pytest.raises(module.ReadinessError):
            module.validate_image(value)


def test_action_group_must_be_full_resource_id():
    good = ("/subscriptions/41fbffdc-1793-44f9-b70f-220ea8a9bc6c/"
            "resourceGroups/rg-textvideo-dev/providers/Microsoft.Insights/"
            "actionGroups/textvideo-dev-operations")
    assert module.validate_action_group(good)
    for invalid in ("", "email@example.com", "action-group-name",
                    "/subscriptions/foo/providers/Microsoft.Insights/actionGroups/x"):
        with pytest.raises(module.ReadinessError):
            module.validate_action_group(invalid)


@pytest.mark.parametrize("label,mutate_job,mutate_alert", [
    ("P1 activation default true", lambda s: s.replace(
        "param activateReconciliation bool = false",
        "param activateReconciliation bool = true", 1), None),
    ("P2 bypass approved cron", lambda s: s.replace(
        "cronExpression: cronExpression", "cronExpression: '* * * * *'", 1), None),
    ("P3 paid flags true", lambda s: s.replace(
        "name: 'ENABLE_PAID_FAL_WORKER', value: 'false'",
        "name: 'ENABLE_PAID_FAL_WORKER', value: 'true'", 1).replace(
        "name: 'ENABLE_WORKER_CONSUMPTION', value: 'false'",
        "name: 'ENABLE_WORKER_CONSUMPTION', value: 'true'", 1), None),
    ("P4 provider fal", lambda s: s.replace(
        "name: 'VIDEO_RENDER_PROVIDER', value: 'disabled'",
        "name: 'VIDEO_RENDER_PROVIDER', value: 'fal'", 1), None),
    ("P5 cost bounds", lambda s: s.replace("replicaRetryLimit: 0", "replicaRetryLimit: 10", 1)
     .replace("parallelism: 1", "parallelism: 20", 1)
     .replace("replicaTimeout: 300", "replicaTimeout: 86400", 1), None),
    ("P6 plaintext provider secret", lambda s: s.replace(
        "name: 'ENABLE_PROJECT_ROUTES', value: 'false'",
        "name: 'ENABLE_PROJECT_ROUTES', value: 'false' }\\n"
        "            { name: 'FAL_API_KEY', value: 'sample-secret'", 1), None),
    ("P7 unconditional extra job", lambda s: s + "\\n" + s[
        s.index("resource scheduledReconciler "):s.index("output scheduledJobName")]
        .replace("resource scheduledReconciler ", "resource rogueJob ", 1)
        .replace("= if (activateReconciliation) {", "= {", 1)
        .replace("job-textvideo-dispatch-reconcile-dev", "rogue-unconditional-job", 1), None),
    ("P8 authorization resource", lambda s: s + """
resource unsafeOwnerGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, 'unsafe-owner')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8e3af657-a8ff-443c-a75c-2fe8c4bcb635')
    principalType: 'ServicePrincipal'
  }
}
""", None),
    ("P9 alert activation true", None, lambda s: s.replace(
        "param activateAlerts bool = false", "param activateAlerts bool = true", 1)),
    ("P10 disable alert notifications", None, lambda s: s.replace(
        "enabled: true", "enabled: false", 1).replace(
        "actionGroups: [actionGroupResourceId]", "actionGroups: []", 1)),
    ("P11 bypass condition", lambda s: s.replace(
        "= if (activateReconciliation) {",
        "= if (activateReconciliation || true) {", 1), None),
])
def test_compiled_arm_rejects_adversarial_mutation(label, mutate_job, mutate_alert):
    changed_job = mutate_job(JOB) if mutate_job is not None else JOB
    changed_alert = mutate_alert(ALERT) if mutate_alert is not None else ALERT
    assert changed_job != JOB or changed_alert != ALERT, label
    with pytest.raises(module.ReadinessError):
        module.validate_sources(changed_job, changed_alert)


def test_compiler_unavailable_fails_closed(monkeypatch):
    import subprocess
    def fail(*args, **kwargs):
        raise FileNotFoundError("bicep")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(module.ReadinessError):
        module.validate_sources(JOB, ALERT)


def test_disabled_mode_creates_no_scheduled_job_and_limits_cron_default():
    assert " = if (activateReconciliation) {" in JOB
    assert "param cronExpression string = '0 * * * *'" in JOB
    assert "activateAlerts bool = false" in ALERT


def test_bicep_cli_supply_chain_is_version_and_digest_pinned():
    workflow = (ROOT / ".github/workflows/dispatch-bicep-validation.yml").read_text()
    assert "releases/latest/" not in workflow
    assert "releases/download/v0.48.1/bicep-linux-x64" in workflow
    assert "b09ec25a9d376c1f8e33ede6ed22b587f915ad68488d5db77a6f9541748c7f6e" in workflow
    assert "sha256sum --check --status" in workflow
    assert workflow.index("sha256sum --check --status") < workflow.index("chmod +x ./bicep")
    assert "persist-credentials: false" in workflow

