import pytest

from fastapi import HTTPException

from job_admin_auth import authorize_job_admin


TOKEN = "a" * 48


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        raising=False,
    )

    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(f"Bearer {TOKEN}")

    assert exc.value.status_code == 503


def test_missing_token_configuration(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.delenv("JOB_ADMIN_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin("Bearer anything")

    assert exc.value.status_code == 503


def test_short_token_configuration(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", "short")

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin("Bearer short")

    assert exc.value.status_code == 503


def test_missing_authorization(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(None)

    assert exc.value.status_code == 401


def test_lone_surrogate_or_invalid_unicode_fails_closed(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)

    for value in ("******", "******", "******"):
        with pytest.raises(HTTPException) as exc:
            authorize_job_admin(value)
        assert exc.value.status_code == 401


def test_non_ascii_job_admin_token_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", "é" * 48)

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(f"******")

    assert exc.value.status_code == 503


def test_incorrect_authorization(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)

    with pytest.raises(HTTPException) as exc:
        authorize_job_admin("Bearer incorrect")

    assert exc.value.status_code == 401


def test_valid_authorization(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)

    assert authorize_job_admin(
        f"Bearer {TOKEN}"
    ) is True
