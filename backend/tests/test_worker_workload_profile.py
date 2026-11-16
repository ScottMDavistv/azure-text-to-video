from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "infra/worker-job.bicep").read_text()


def test_consumption_profile_is_explicit():
    assert "workloadProfileName: 'Consumption'" in SOURCE


def test_worker_uses_manual_trigger():
    assert "triggerType: 'Manual'" in SOURCE


def test_worker_is_inspection_only():
    assert "'worker_runtime.py'" in SOURCE
    assert "'--check'" in SOURCE


def test_inspection_worker_has_no_provider_secret():
    assert "falKeyVaultSecretUri" not in SOURCE
    assert "secretRef: 'fal-api-key'" not in SOURCE
    assert "secrets: []" in SOURCE


def test_rendering_flags_remain_present():
    assert "name: 'ENABLE_LIVE_VIDEO_RENDERING'" in SOURCE
    assert "name: 'ENABLE_WORKER_CONSUMPTION'" in SOURCE
