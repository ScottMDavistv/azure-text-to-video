import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate-worker-infrastructure.py"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "worker_infra_validator",
        SCRIPT,
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_safe_infrastructure():
    result = load_validator().validate_worker_infrastructure()

    assert result["valid"] is True
    assert result["mode"] == "inspection_only"


def test_deployment_not_approved():
    result = load_validator().validate_worker_infrastructure()

    assert result["deployment_approved"] is False


def test_manual_trigger():
    manifest = json.loads(
        (
            ROOT / "infra/worker-job.intent.json"
        ).read_text()
    )

    assert manifest["trigger_type"] == "Manual"
    assert manifest["auto_execution_enabled"] is False


def test_no_public_ingress():
    manifest = json.loads(
        (
            ROOT / "infra/worker-job.intent.json"
        ).read_text()
    )

    assert manifest["public_ingress_enabled"] is False


def test_inspection_command():
    manifest = json.loads(
        (
            ROOT / "infra/worker-job.intent.json"
        ).read_text()
    )

    assert manifest["execution_command"] == [
        "python",
        "worker_runtime.py",
        "--check",
    ]


def test_missing_manual_trigger_rejected(monkeypatch):
    module = load_validator()
    original = module.BICEP_PATH

    class FakePath:
        def read_text(self):
            return original.read_text().replace(
                "triggerType: 'Manual'",
                "triggerType: 'Event'",
            )

    monkeypatch.setattr(module, "BICEP_PATH", FakePath())

    with pytest.raises(module.WorkerInfrastructureError):
        module.validate_worker_infrastructure()


def test_auto_execution_intent_rejected(monkeypatch):
    module = load_validator()
    original = module.INTENT_PATH

    class FakePath:
        def read_text(self):
            data = json.loads(original.read_text())
            data["auto_execution_enabled"] = True
            return json.dumps(data)

    monkeypatch.setattr(module, "INTENT_PATH", FakePath())

    with pytest.raises(module.WorkerInfrastructureError):
        module.validate_worker_infrastructure()
