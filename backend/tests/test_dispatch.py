"""Legacy queue API now uses the durable dispatch service."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from main import app
from dispatch_recovery import DispatchConflict, DispatchUncertain


@pytest.fixture(autouse=True)
def local_dev(monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")


client = TestClient(app)


def test_queue_delivery_success():
    job_id = str(uuid4())
    with patch("jobs.transition_job", return_value={"status": "queued"}) as transition, \
         patch("dispatch_recovery.dispatch_queued_job",
               return_value={"dispatched": True, "status": "sent"}) as dispatch:
        response = client.post(f"/jobs/{job_id}/queue")
    assert response.status_code == 200
    assert response.json()["dispatched"] is True
    transition.assert_called_once_with(job_id, "queued", dispatchState="pending")
    dispatch.assert_called_once_with(job_id)


def test_queue_delivery_uncertain():
    job_id = str(uuid4())
    with patch("jobs.transition_job", return_value={"status": "queued"}), \
         patch("dispatch_recovery.dispatch_queued_job",
               side_effect=DispatchUncertain("lost acknowledgement")):
        response = client.post(f"/jobs/{job_id}/queue")
    assert response.status_code == 503
    assert "recovery" in response.json()["detail"].lower()


def test_duplicate_queue_request_does_not_dispatch():
    job_id = str(uuid4())
    with patch("jobs.transition_job", side_effect=ValueError("already queued")), \
         patch("dispatch_recovery.dispatch_queued_job") as dispatch:
        response = client.post(f"/jobs/{job_id}/queue")
    assert response.status_code == 409
    dispatch.assert_not_called()
