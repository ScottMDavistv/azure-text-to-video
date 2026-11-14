from unittest.mock import patch

from api_access import (
    api_auth_configured,
    api_auth_required,
    check_api_authorization,
    protected_job_path,
)


TOKEN = "a" * 48


def test_auth_disabled_by_default():
    with patch.dict(
        "os.environ",
        {"REQUIRE_JOB_API_AUTH": ""},
    ):
        assert api_auth_required() is False


def test_auth_explicitly_enabled():
    with patch.dict(
        "os.environ",
        {"REQUIRE_JOB_API_AUTH": "true"},
    ):
        assert api_auth_required() is True


def test_short_token_not_configured():
    with patch.dict(
        "os.environ",
        {"API_ACCESS_TOKEN": "short"},
    ):
        assert api_auth_configured() is False


def test_full_token_configured():
    with patch.dict(
        "os.environ",
        {"API_ACCESS_TOKEN": TOKEN},
    ):
        assert api_auth_configured() is True


def test_valid_token():
    with patch.dict(
        "os.environ",
        {"API_ACCESS_TOKEN": TOKEN},
    ):
        assert check_api_authorization(
            f"Bearer {TOKEN}"
        )


def test_wrong_token():
    with patch.dict(
        "os.environ",
        {"API_ACCESS_TOKEN": TOKEN},
    ):
        assert not check_api_authorization(
            "Bearer wrong"
        )


def test_missing_token():
    with patch.dict(
        "os.environ",
        {"API_ACCESS_TOKEN": TOKEN},
    ):
        assert not check_api_authorization(None)


def test_job_routes_are_protected():
    assert protected_job_path("/jobs")
    assert protected_job_path("/jobs/abc")
    assert protected_job_path("/jobs/abc/queue")
    assert protected_job_path("/jobs/abc/video")


def test_health_routes_excluded():
    assert not protected_job_path("/")
    assert not protected_job_path("/health")
    assert not protected_job_path("/admin/jobs")
