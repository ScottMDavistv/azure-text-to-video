"""Read-only manual job-recovery inventory: authorization and bounded results."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError
from fastapi.testclient import TestClient

from main import app
from stale_jobs import find_stale_jobs


TOKEN = "a" * 48
NOW = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)


def test_disabled_stale_endpoint_never_calls_storage(monkeypatch):
    monkeypatch.delenv("ENABLE_JOB_ADMIN_ENDPOINTS", raising=False)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_DEV", raising=False)
    with patch("job_admin_routes.stale_jobs.find_stale_jobs") as listing:
        response = TestClient(app).get("/admin/jobs/stale")
    assert response.status_code == 503
    listing.assert_not_called()


def test_unauthorized_stale_endpoint_never_calls_storage(monkeypatch):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    with patch("job_admin_routes.stale_jobs.find_stale_jobs") as listing:
        response = TestClient(app).get("/admin/jobs/stale")
    assert response.status_code == 401
    listing.assert_not_called()


def test_stale_endpoint_is_read_only_and_bounded(monkeypatch):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    expected = [{"job_id": str(uuid4()), "status": "processing"}]
    with patch("job_admin_routes.stale_jobs.find_stale_jobs", return_value=expected) as listing:
        response = TestClient(app).get(
            "/admin/jobs/stale?stale_minutes=120&limit=2",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["jobs"] == expected
    assert response.json()["recovery"] == "manual_confirmation_required"
    assert response.json()["limit"] == 2
    listing.assert_called_once_with(stale_minutes=120, limit=2)


@pytest.mark.parametrize("query", ["?limit=51", "?limit=0", "?stale_minutes=0",
                                   "?stale_minutes=10081", "?limit=true"])
def test_invalid_query_parameters_rejected_before_storage(monkeypatch, query):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    with patch("job_admin_routes.stale_jobs.find_stale_jobs") as listing:
        response = TestClient(app).get(
            "/admin/jobs/stale" + query,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 422
    listing.assert_not_called()


def test_storage_failure_is_not_reported_as_no_stale_jobs(monkeypatch):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    with patch("job_admin_routes.stale_jobs.find_stale_jobs",
               side_effect=HttpResponseError(message="storage down", status_code=503)):
        response = TestClient(app).get(
            "/admin/jobs/stale", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 503


def test_inventory_stops_after_limit():
    table = MagicMock()
    candidates = []
    for _ in range(4):
        candidates.append({
            "RowKey": str(uuid4()),
            "status": "processing",
            "startedAt": (NOW - timedelta(hours=2)).isoformat(),
        })
    table.query_entities.return_value = iter(candidates)
    with patch("stale_jobs.get_jobs_table_client", return_value=table):
        result = find_stale_jobs(now=NOW, limit=2)
    assert len(result) == 2
    assert result[0]["job_id"] == candidates[0]["RowKey"]


@pytest.mark.parametrize("limit", [True, 0, 51, 2.5])
def test_bad_inventory_limits_fail_before_storage(limit):
    with patch("stale_jobs.get_jobs_table_client") as table:
        with pytest.raises(ValueError):
            find_stale_jobs(limit=limit)
        table.assert_not_called()
