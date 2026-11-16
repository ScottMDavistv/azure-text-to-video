import pytest
from unittest.mock import patch
from uuid import uuid4

from azure.core.exceptions import AzureError, ResourceNotFoundError
from fastapi.testclient import TestClient

from main import app

@pytest.fixture(autouse=True)
def local_legacy_api_behavior(monkeypatch):
    # Existing route-behavior tests run ONLY in an explicit local-dev
    # exemption. Deployment and security tests verify default-deny separately.
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")


client = TestClient(app)


def test_recovery_success():
    job_id = str(uuid4())

    with (
        patch("main.get_jobs_table_client") as table,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        table.return_value.get_entity.return_value = {
            "status": "queued"
        }

        response = client.post(
            f"/jobs/{job_id}/retry-dispatch"
        )

        assert response.status_code == 200
        assert response.json()["recovery"] is True
        enqueue.assert_called_once_with(job_id)


def test_recovery_rejects_unqueued_job():
    job_id = str(uuid4())

    with (
        patch("main.get_jobs_table_client") as table,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        table.return_value.get_entity.return_value = {
            "status": "completed"
        }

        response = client.post(
            f"/jobs/{job_id}/retry-dispatch"
        )

        assert response.status_code == 409
        enqueue.assert_not_called()


def test_recovery_missing_job():
    job_id = str(uuid4())

    with patch("main.get_jobs_table_client") as table:
        table.return_value.get_entity.side_effect = (
            ResourceNotFoundError("Not found")
        )

        response = client.post(
            f"/jobs/{job_id}/retry-dispatch"
        )

        assert response.status_code == 404


def test_recovery_queue_unavailable():
    job_id = str(uuid4())

    with (
        patch("main.get_jobs_table_client") as table,
        patch("job_queue.enqueue_job") as enqueue,
    ):
        table.return_value.get_entity.return_value = {
            "status": "queued"
        }
        enqueue.side_effect = AzureError("Unavailable")

        response = client.post(
            f"/jobs/{job_id}/retry-dispatch"
        )

        assert response.status_code == 503
