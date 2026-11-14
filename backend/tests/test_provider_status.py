from unittest.mock import patch

from provider_status import (
    check_runtime_environment,
    get_render_configuration_status,
)


def test_disabled_provider():
    with patch.dict(
        "os.environ",
        {"VIDEO_RENDER_PROVIDER": "disabled"},
    ):
        result = get_render_configuration_status()

    assert result["rendering_enabled"] is False
    assert result["integration_status"] == "disabled"


def test_fal_without_credentials():
    with patch.dict(
        "os.environ",
        {
            "VIDEO_RENDER_PROVIDER": "fal",
            "FAL_KEY": "",
        },
    ):
        result = get_render_configuration_status()

    assert result["credential_configured"] is False
    assert result["rendering_enabled"] is False


def test_fal_credentials_do_not_enable_rendering():
    secret = "test-placeholder-secret"

    with patch.dict(
        "os.environ",
        {
            "VIDEO_RENDER_PROVIDER": "fal",
            "FAL_KEY": secret,
        },
    ):
        result = get_render_configuration_status()

    assert result["credential_configured"] is True
    assert result["rendering_enabled"] is False
    assert secret not in str(result)


def test_runtime_environment():
    with patch.dict(
        "os.environ",
        {
            "VIDEO_RENDER_PROVIDER": "disabled",
            "AZURE_CLIENT_ID": "test-client",
            "AZURE_STORAGE_ACCOUNT": "test-storage",
            "AZURE_JOBS_QUEUE": "textvideo-jobs",
        },
    ):
        result = check_runtime_environment()

    assert result["azure_client_id_configured"] is True
    assert result["storage_account_configured"] is True
    assert result["queue_name"] == "textvideo-jobs"
