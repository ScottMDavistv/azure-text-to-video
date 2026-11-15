from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[2]


def load_validator():
    location = (
        ROOT /
        "scripts/validate-fal-keyvault-iac.py"
    )

    spec = importlib.util.spec_from_file_location(
        "fal_keyvault_validator",
        location,
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_keyvault_configuration_is_valid():
    result = load_validator().validate()

    assert result["valid"]
    assert result["deployment_approved"] is False


def test_secret_uses_keyvault_reference():
    source = (
        ROOT / "infra/worker-job.bicep"
    ).read_text()

    assert "keyVaultUrl: falKeyVaultSecretUri" in source
    assert "identity: identity.id" in source


def test_environment_uses_secret_reference():
    source = (
        ROOT / "infra/worker-job.bicep"
    ).read_text()

    assert "name: 'FAL_KEY'" in source
    assert "secretRef: 'fal-api-key'" in source


def test_no_hardcoded_fal_credential():
    source = (
        ROOT / "infra/worker-job.bicep"
    ).read_text()

    assert "name: 'fal-api-key'" in source
    assert "keyVaultUrl: falKeyVaultSecretUri" in source

    # Do not allow a literal credential assignment
    # in the Bicep secret definition.
    secret_block = source.split(
        "name: 'fal-api-key'",
        1,
    )[1].split("}", 1)[0]

    assert "value:" not in secret_block


def test_worker_remains_inspection_only():
    result = load_validator().validate()

    assert result["worker_mode"] == "inspection_only"
