import pytest
from unittest.mock import patch
from uuid import uuid4

from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(autouse=True)
def local_legacy_api_behavior(monkeypatch):
    # Existing route-behavior tests run ONLY in an explicit local-dev
    # exemption. Deployment and security tests verify default-deny separately.
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")


client = TestClient(app)


def test_queue_delivery_success():
    job_id = str(uuid4())

    with (
        patch("jobs.transition_job") as transition,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        transition.return_value = {"status": "queued"}

        response = client.post(f"/jobs/{job_id}/queue")

        assert response.status_code == 200
        assert response.json()["dispatched"] is True
        transition.assert_called_once()
        enqueue.assert_called_once_with(job_id)


def test_queue_delivery_failure():
    job_id = str(uuid4())

    with (
        patch("jobs.transition_job") as transition,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        transition.return_value = {"status": "queued"}
        enqueue.side_effect = AzureError("Queue unavailable")

        response = client.post(f"/jobs/{job_id}/queue")

        assert response.status_code == 503
        assert "recovery" in response.json()["detail"].lower()


def test_duplicate_queue_request():
    job_id = str(uuid4())

    with (
        patch("jobs.transition_job") as transition,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        transition.side_effect = ValueError(
            "Cannot transition from queued to queued"
        )

        response = client.post(f"/jobs/{job_id}/queue")

        assert response.status_code == 409
        enqueue.assert_not_called()
