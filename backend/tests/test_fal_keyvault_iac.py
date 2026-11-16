from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]


def load_validator():
    location = ROOT / "scripts/validate-fal-keyvault-iac.py"
    spec = importlib.util.spec_from_file_location("worker_iac_validator", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_non_billable_worker_configuration_valid():
    result = load_validator().validate()
    assert result["valid"] is True
    assert result["deployment_approved"] is False


def test_non_billable_worker_has_no_fal_secret_reference():
    source = (ROOT / "infra/worker-job.bicep").read_text()
    assert "falKeyVaultSecretUri" not in source
    assert "fal-api-key" not in source
    assert "name: 'FAL_KEY'" not in source
    assert "secrets: []" in source


def test_worker_uses_separate_dedicated_identity():
    result = load_validator().validate()
    assert result["worker_identity"] == "id-textvideo-worker-dev"
    assert result["azure_rbac_verified"] is False


def test_worker_remains_inspection_only():
    assert load_validator().validate()["worker_mode"] == "inspection_only"
