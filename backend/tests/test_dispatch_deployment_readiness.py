"""Non-networked P0 readiness policy regression tests."""
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


@pytest.mark.parametrize("needle", [
    "param activateReconciliation bool = false",
    "if (activateReconciliation)",
    "secrets: []",
    "replicaRetryLimit: 0",
    "parallelism: 1",
    "name: 'ENABLE_PAID_FAL_WORKER'",
    "name: 'ENABLE_LIVE_VIDEO_RENDERING'",
    "name: 'AZURE_CLIENT_ID'",
    " : [ '--check' ]",
    "@allowed([ '*/15 * * * *', '0 * * * *' ])",
])
def test_removed_job_safety_boundary_fails(needle):
    with pytest.raises(module.ReadinessError):
        module.validate_sources(JOB.replace(needle, "REMOVED", 1), ALERT)


@pytest.mark.parametrize("needle", [
    "param activateAlerts bool = false",
    "if (activateAlerts)",
    "ContainerAppConsoleLogs_CL",
    "Log_s contains",
    "actionGroups: [actionGroupResourceId]",
])
def test_removed_monitor_safety_boundary_fails(needle):
    with pytest.raises(module.ReadinessError):
        module.validate_sources(JOB, ALERT.replace(needle, "REMOVED", 1))


def test_paid_credentials_are_disallowed():
    with pytest.raises(module.ReadinessError):
        module.validate_sources(JOB + "\n// FAL_KEY\n", ALERT)


def test_disabled_mode_creates_no_scheduled_job_and_limits_cron_default():
    assert " = if (activateReconciliation) {" in JOB
    assert "param cronExpression string = '0 * * * *'" in JOB
    assert "activateReconciliation bool = false" in JOB
    assert "activateAlerts bool = false" in ALERT

def test_bicep_cli_supply_chain_is_version_and_digest_pinned():
    workflow = (ROOT / ".github/workflows/dispatch-bicep-validation.yml").read_text()
    assert "releases/latest/" not in workflow
    assert "releases/download/v0.48.1/bicep-linux-x64" in workflow
    assert "b09ec25a9d376c1f8e33ede6ed22b587f915ad68488d5db77a6f9541748c7f6e" in workflow
    assert "sha256sum --check --status" in workflow
    assert workflow.index("sha256sum --check --status") < workflow.index("chmod +x ./bicep")
    assert "persist-credentials: false" in workflow

