"""Worker image pinning negative-path regression tests (offline)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "worker-image-digest.py"
WHATIF = ROOT / "scripts" / "cloudshell-worker-whatif.sh"
BICEP = ROOT / "infra" / "worker-job.bicep"
spec = importlib.util.spec_from_file_location("worker_image_digest", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

BASE = "acrtextvideo1794671698.azurecr.io/textvideo-worker"
TAG = "692bb1b7f7bd467f1d64e7288115424d21506c12"
DIGEST = "sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646"
IMAGE = BASE + "@" + DIGEST


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def test_exact_audited_immutable_image_validates():
    assert gate.validate_image(IMAGE) == IMAGE
    result = cli("--validate", IMAGE)
    assert result.returncode == 0
    assert result.stdout.strip() == IMAGE


@pytest.mark.parametrize("candidate", [
    BASE + ":" + TAG, BASE + "@sha256:" + "a" * 63,
    BASE + "@sha256:" + "A" * 64, BASE + "@sha256:" + "z" * 64,
    "evil.example/textvideo-worker@" + DIGEST,
    "acrtextvideo1794671698.azurecr.io/render-api@" + DIGEST,
    IMAGE + "\nEXTRA", "", BASE + "@sha512:" + "a" * 128
])
def test_invalid_image_rejected(candidate):
    assert cli("--validate", candidate).returncode == 2


def test_exact_read_only_manifest_lookup():
    calls = []
    def stub(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"digest": DIGEST}))
    assert gate.resolve_digest(TAG, runner=stub) == IMAGE
    assert calls[0][0] == [
        "az", "acr", "manifest", "show-metadata",
        "--registry", "acrtextvideo1794671698",
        "--name", "textvideo-worker:" + TAG, "--output", "json"]
    assert calls[0][1]["timeout"] == 45
    assert not calls[0][1].get("shell", False)


@pytest.mark.parametrize("response", [
    {}, [], {"digest": None}, {"digest": "sha256:" + "A" * 64},
    {"digest": "sha256:" + "a" * 63}
])
def test_invalid_acr_metadata_blocks(response):
    def stub(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(response))
    with pytest.raises(ValueError):
        gate.resolve_digest(TAG, runner=stub)


def test_failed_acr_lookup_never_falls_back():
    def stub(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="")
    with pytest.raises(RuntimeError, match="unavailable"):
        gate.resolve_digest(TAG, runner=stub)


def test_invalid_tag_never_calls_azure():
    def stub(*args, **kwargs):
        raise AssertionError("unexpected Azure command")
    with pytest.raises(ValueError):
        gate.resolve_digest("../bad", runner=stub)


def test_whatif_restricts_image_and_immutable_source_commit():
    script = WHATIF.read_text()
    assert 'if [[ "$IMAGE" != "$VERIFIED_IMAGE" ]]' in script
    assert IMAGE in script
    assert "?ref=$REVIEWED_TEMPLATE_COMMIT" in script
    assert 'grep -Fq "@allowed(["' in script
    assert 'grep -Fq "secrets: []"' in script
    assert "triggerType: 'Manual'" in script
    assert "^[0-9a-f]{40}$" in script
    assert "az deployment group what-if" in script
    assert "az deployment group create" not in script
    assert "az containerapp job start" not in script
    assert subprocess.run(["bash", "-n", str(WHATIF)]).returncode == 0
    assert subprocess.run(["bash", str(WHATIF), BASE + ":" + TAG, "a" * 40],
                          capture_output=True).returncode == 2
    assert subprocess.run(["bash", str(WHATIF), IMAGE, "invalid"],
                          capture_output=True).returncode == 2


def test_bicep_is_exact_digest_pinned_and_inspection_only():
    template = BICEP.read_text()
    assert "@minLength(122)" in template
    assert "@maxLength(122)" in template
    assert "@allowed([" in template
    assert "'" + IMAGE + "'" in template
    assert "param workerImage string" in template
    assert "triggerType: 'Manual'" in template
    assert "workloadProfileName: 'Consumption'" in template
    assert "'worker_runtime.py'" in template
    assert "'--check'" in template
    for flag in ("ENABLE_WORKER_CONSUMPTION", "ENABLE_LIVE_VIDEO_RENDERING",
                 "VIDEO_RENDER_PROVIDER"):
        assert flag in template
    assert "value: 'disabled'" in template
    assert "value: 'false'" in template
    assert "secrets: []" in template
