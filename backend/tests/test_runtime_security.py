from unittest.mock import patch

from runtime_security import (
    get_runtime_security_status,
)


def test_disabled_defaults():
    with patch.dict(
        "os.environ",
        {
            "REQUIRE_JOB_API_AUTH": "",
            "ENABLE_JOB_ADMIN_ENDPOINTS": "",
            "ENABLE_VIDEO_DOWNLOADS": "",
        },
    ):
        result = get_runtime_security_status()

    assert result["job_api_auth_required"] is True
    assert result["admin_endpoints_enabled"] is False
    assert result["video_downloads_enabled"] is False
    assert result["public_exposure_approved"] is False


def test_auth_configured_without_exposing_secret():
    secret = "test-secret-" + "x" * 40

    with patch.dict(
        "os.environ",
        {
            "REQUIRE_JOB_API_AUTH": "true",
            "API_ACCESS_TOKEN": secret,
        },
    ):
        result = get_runtime_security_status()

    assert result["job_api_auth_required"] is True
    assert result["job_api_auth_configured"] is True
    assert secret not in str(result)


def test_no_user_ownership_claim():
    result = get_runtime_security_status()

    assert (
        result["user_ownership_authorization_implemented"]
        is False
    )
