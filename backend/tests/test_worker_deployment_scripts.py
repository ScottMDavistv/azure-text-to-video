from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]

WHATIF = ROOT / "scripts/cloudshell-worker-whatif.sh"
IMAGE_TAG = ROOT / "scripts/worker-image-tag.sh"


def test_scripts_exist():
    assert WHATIF.exists()
    assert IMAGE_TAG.exists()


def test_whatif_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(WHATIF)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


def test_image_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(IMAGE_TAG)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


def test_whatif_has_no_deployment_apply():
    content = WHATIF.read_text()

    assert "az deployment group what-if" in content
    assert "az deployment group create" not in content
    assert "az containerapp job start" not in content


def test_whatif_pins_subscription():
    content = WHATIF.read_text()

    assert (
        "41fbffdc-1793-44f9-b70f-220ea8a9bc6c"
        in content
    )


def test_image_tag_is_immutable_sha():
    result = subprocess.run(
        ["bash", str(IMAGE_TAG)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip().startswith(
        "acrtextvideo1794671698.azurecr.io/"
        "textvideo-worker:"
    )

    tag = result.stdout.strip().rsplit(":", 1)[-1]

    assert len(tag) == 40
    assert all(c in "0123456789abcdef" for c in tag)
