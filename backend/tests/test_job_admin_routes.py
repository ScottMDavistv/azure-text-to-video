from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from job_admin_routes import (
    cancel_job_endpoint,
    get_job_endpoint,
    list_jobs_endpoint,
)


TOKEN = "z" * 48
AUTH = f"Bearer {TOKEN}"


@pytest.fixture
def admin_enabled(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        "true",
    )
    monkeypatch.setenv(
        "JOB_ADMIN_TOKEN",
        TOKEN,
    )


def test_listing_requires_authentication(admin_enabled):
    with patch(
        "job_admin_routes.job_management.list_jobs"
    ) as service:
        with pytest.raises(HTTPException) as exc:
            list_jobs_endpoint(
                authorization=None,
            )

    assert exc.value.status_code == 401
    service.assert_not_called()


def test_listing_disabled_by_default(monkeypatch):
    monkeypatch.delenv(
        "ENABLE_JOB_ADMIN_ENDPOINTS",
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        list_jobs_endpoint(
            authorization=AUTH,
        )

    assert exc.value.status_code == 503


def test_listing_success(admin_enabled):
    expected = {
        "jobs": [],
        "count": 0,
        "next_cursor": None,
    }

    with patch(
        "job_admin_routes.job_management.list_jobs",
        return_value=expected,
    ):
        result = list_jobs_endpoint(
            limit=20,
            authorization=AUTH,
        )

    assert result == expected


def test_cancel_requires_authentication(admin_enabled):
    with patch(
        "job_admin_routes.job_management.cancel_job"
    ) as service:
        with pytest.raises(HTTPException) as exc:
            cancel_job_endpoint(
                str(uuid4()),
                authorization="Bearer wrong",
            )

    assert exc.value.status_code == 401
    service.assert_not_called()


def test_job_lookup_requires_authentication(admin_enabled):
    with patch(
        "job_admin_routes.job_management.get_job_details"
    ) as service:
        with pytest.raises(HTTPException) as exc:
            get_job_endpoint(
                str(uuid4()),
                authorization=None,
            )

    assert exc.value.status_code == 401
    service.assert_not_called()


def test_cancel_success(admin_enabled):
    job_id = str(uuid4())

    with patch(
        "job_admin_routes.job_management.cancel_job",
        return_value={
            "job_id": job_id,
            "status": "cancelled",
        },
    ):
        result = cancel_job_endpoint(
            job_id,
            authorization=AUTH,
        )

    assert result["status"] == "cancelled"
