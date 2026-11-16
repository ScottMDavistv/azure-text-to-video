"""Recovery API must use durable lease arbitration."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient
from dispatch_recovery import DispatchConflict, DispatchUncertain
from main import app


@pytest.fixture(autouse=True)
def local_dev(monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")


client = TestClient(app)


def test_recovery_success():
    job_id = str(uuid4())
    with patch("dispatch_recovery.dispatch_queued_job",
               return_value={"dispatched": True, "status": "sent"}) as dispatch:
        response = client.post(f"/jobs/{job_id}/retry-dispatch")
    assert response.status_code == 200
    assert response.json()["recovery"] is True
    dispatch.assert_called_once_with(job_id)


def test_recovery_already_dispatched_has_no_new_message():
    job_id = str(uuid4())
    with patch("dispatch_recovery.dispatch_queued_job",
               return_value={"dispatched": False,
                             "status": "already_dispatched"}):
        response = client.post(f"/jobs/{job_id}/retry-dispatch")
    assert response.status_code == 200
    assert response.json()["dispatched"] is False


def test_recovery_rejects_unqueued_job():
    job_id = str(uuid4())
    with patch("dispatch_recovery.dispatch_queued_job",
               side_effect=DispatchConflict("Only queued jobs")):
        response = client.post(f"/jobs/{job_id}/retry-dispatch")
    assert response.status_code == 409


def test_recovery_missing_job():
    job_id = str(uuid4())
    with patch("dispatch_recovery.dispatch_queued_job",
               side_effect=ResourceNotFoundError("Not found")):
        response = client.post(f"/jobs/{job_id}/retry-dispatch")
    assert response.status_code == 404


def test_recovery_queue_outcome_uncertain():
    job_id = str(uuid4())
    with patch("dispatch_recovery.dispatch_queued_job",
               side_effect=DispatchUncertain("lost response")):
        response = client.post(f"/jobs/{job_id}/retry-dispatch")
    assert response.status_code == 503
    assert "lease" in response.json()["detail"].lower()
